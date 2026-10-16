# HANDOFF — data-privacy-services (DPS)

Last verified: — (filled by the release, M5 Task 28: date, commit and the summary line of every gate in §12)

Build and deploy facts only, kept current (the Wega rule): update this file in the same change as
anything that alters an image, a port, a setting, an extra, a probe, a label or a deploy step. What
each milestone delivered and its exit evidence are in `CHANGELOG.md`; the per-task build records in
`docs/superpowers/build/`. §3's table is generated (`python tools/settings_table.py`), and
`tests/test_handoff.py` fails while a setting, a compose service, an OpenShift role or a mounted
secret is missing here.

**Scope of this revision (2026-10-16, the v1.0.0 release merge): M0–M5 and every finish stream
on `main`** — the UI scanner (M4), the security review (`fsec`, `fsecc`), the arbitrary-UID images
and the `full` stack (Task 24, `fimg`), the OpenShift manifests (Task 25, `fos`), the ten-minute
demo and its deck (Task 26, `fdemo`), this documentation pass (Task 27, `fdocs`) and the fixes of
the live LLM run (`fllm`). §1–§13 are the current facts; §14 keeps the build records of the merges
that brought them (measurements, proofs and the traps each stream found).

## 1. What ships

| Artefact | Kind | Notes |
|---|---|---|
| `data-privacy-services` | container image | FastAPI API + compiled console in one process on port **8400**; non-root uid **10001** (primary group 0), or any arbitrary UID in group 0 with a read-only root (§2). One image, three roles: `app` (`uvicorn dps.app:create_app --factory`), `worker` (`python -m dps.worker`, ops port **9400**) and `migrate` (`python -m dps.migrate`, a one-shot / Job). Extras baked in: `fpe`, `docs`, `db`, `connectors`, `tabular`; `--build-arg WITH_NER=1` adds `ner` + `en_core_web_sm`. The `synth-ml` extra (MOSTLY AI, ≈ 2 GB with torch) is never in this image: a bank-side opt-in after the licence and size review (`--build-arg EXTRAS=synth-ml`, §2) |
| `data-privacy-services-browser` | container image | the `browser-worker` role (`python -m dps.browser_worker`: claims `scan` jobs only; Chromium 1187, ops port **9401**) built from `Dockerfile.browser` on `mcr.microsoft.com/playwright:v1.55.0-noble` with the `scanner` extra; uid 10001 with group 0, arbitrary-UID safe like the app image (§2) |
| `dps` | Python 3.12 package, version 1.0.0 | Privacy Engine, Layer 1 API and documents, the job platform (PostgreSQL, Oracle, MongoDB), the UI scanner, tabular synthesis, admin, reports, policy store. Extras: see the table below |
| `frontend/dist` | Vite + React build | compiled inside the image (stage 1) and served by the app at `/`; never committed (`.gitignore`, `.dockerignore`) |
| `deploy/openshift` | kustomize | base + components + `dev`/`prod` overlays + `release-example`: Deployments `dps-app`, `dps-worker`, `dps-browser-worker` (each after its `wait-for-schema` init container), the Job `dps-migrate-<release>`, the CronJob `dps-migrate-maintain` (§7) |
| `tools/` (copied into the image at `/app/tools`) | operator scripts | `smoke_test.py` (end-to-end checks against a stack), `seed_sources.py` (+ `seed_model.py`, `seed_oracle.py`, `seed_mongo.py`: one synthetic customer space in PostgreSQL, Oracle and MongoDB), `demo_m2.py` (10-table UAT copy, in place, append), `bench_connector.py` (measured rows/s), `reveal_rows.py` (local reveal of output rows with the caller's key), `console_check.py` (browser check of the M1 pages), `eval_detectors.py`, `make_demo_docs.py`, `settings_table.py`, `scratch_pg.sh` (PostgreSQL 16 without Docker), `demo_client.py` (the tools' API client) and `flow_report.py` (every contract §9 route once; Task 24), `openshift_check.py` (the manifests' release gate; Task 25), `demo_seed.py`, `e2e_demo.py` and `build_deck.py` (the ten-minute demo and its deck; Task 26) |
| Operator modules | `python -m …` in the image | `dps.migrate` (DDL, `--verify`, `--maintain`), `dps.worker`, `dps.keys_cli` (DB-backed API keys), `dps.rekey` (KEK rotation) |

Python extras (`pyproject.toml`):

| Extra | Contents | In the image |
|---|---|---|
| `fpe` | `fastfpe` (FF1, NIST SP 800-38G); used only with `DPS_FPE_ENABLED=true` | yes |
| `docs` | pypdf, pdfplumber, pypdfium2, reportlab, python-docx, Pillow — the app imports the document pipeline at startup | yes |
| `db` | asyncpg (metadata database, PostgreSQL connector) | yes |
| `connectors` | sqlglot, pyarrow, boto3, `oracledb==26.0.0` (thin), pymongo (`AsyncMongoClient`) | yes |
| `tabular` | numpy, scipy (Gaussian copula, fidelity/privacy metrics) | yes |
| `scanner` | `playwright==1.55.0`, Pillow (the browser-worker) | browser image only, never the app image |
| `ner` | presidio-analyzer, spaCy | only with `WITH_NER=1` |
| `vertex` | google-genai (laptop path to Gemini) | no |
| `synth-ml` | `mostlyai[local]`, pandas (optional MOSTLY AI engine) | never |
| `uitest`, `e2e` | `playwright==1.55.0` (console checks) | no |
| `deck` | `python-pptx` (`tools/build_deck.py`, the showcase deck) | no (dev only) |
| `dev` | pytest, pytest-asyncio, pytest-timeout, ruff, mypy, types-PyYAML | no |

## 2. Build

```bash
docker build -t data-privacy-services:local .                                      # what compose builds (tag ${DPS_IMAGE_TAG:-local})
docker build --platform linux/amd64 -t <registry>/data-privacy-services:<semver> .   # amd64 from a Mac
docker build --build-arg WITH_NER=1 -t <registry>/data-privacy-services:<semver>-ner .
docker build --build-arg EXTRAS=synth-ml -t <registry>/data-privacy-services:<semver>-ml .   # opt-in extras (bank side)
docker build -f Dockerfile.browser -t data-privacy-services-browser:local .               # the browser-worker (compose: --profile scanner build)
docker build --platform linux/amd64 -f Dockerfile.browser -t <registry>/data-privacy-services-browser:<semver> .
```

Tag every image with the semver **and** the git sha; never deploy `latest` alone. The Dockerfile:

- stage 1 `node:22-slim` on the **build host's** platform (`FROM --platform=$BUILDPLATFORM`: the dist
  is plain JS/CSS, so an amd64 build on an arm64 Mac never runs npm under emulation): `npm ci`
  (lockfile only, no `npm install` fallback) + `npm run build`;
- stage 2 `python:3.12-slim`: `tesseract-ocr` + `tesseract-ocr-eng` from apt and `ENV
  DPS_OCR_PROVIDER=auto` (local OCR when no Azure endpoint is configured); `uv` 0.11.21 pinned
  (`COPY --from=ghcr.io/astral-sh/uv:0.11.21`); `uv pip install --system --no-cache -e
  ".[fpe,docs,db,connectors,tabular]"` (`WITH_NER=1`: `.[fpe,ner,docs,db,connectors,tabular]` and
  `python -m spacy download en_core_web_sm`); the console at `/app/frontend/dist`; `tools/` at
  `/app/tools`; `/app/var/{artefacts,exports}` owned by uid 10001 and group 0; `--build-arg
  EXTRAS=a,b` adds further extras to either `WITH_NER` variant (lower-case names only, anything
  else stops the build with exit 2);
- the package is installed **editable** on purpose: `dps/app.py` serves `<source root>/frontend/dist`
  (a wheel install starts without the console: `frontend_disabled` in the log);
- `USER 10001`, `EXPOSE 8400`, a Python `urllib` `HEALTHCHECK` on `http://127.0.0.1:8400/health`
  (15 s interval and start period); `ENTRYPOINT docker/entrypoint.sh` (`umask 0002`; runs `python -m dps.migrate`
  first only when `DPS_MIGRATIONS_MODE=auto`, `DPS_PG_HOST` is set and the command is `uvicorn` —
  single-container runs; compose and OpenShift use the separate migrate one-shot/Job);
  `CMD uvicorn dps.app:create_app --factory --host 0.0.0.0 --port 8400 --no-access-log`.

**Arbitrary UID, read-only root (Task 24; record §14.6).** OpenShift runs pods under an arbitrary
UID in group 0 with a read-only root filesystem. The image user 10001 has group 0 as its primary
group (`useradd --uid 10001 --gid 0`); `/app` is group 0, readable and never writable by group 0
(`chmod -R g+rX,g-w /app` — never `chmod -R g=u /app`, which would make the code writable by every
OpenShift UID); `/app/var` is group-writable (`chmod -R g=u /app/var`) with setgid directories, and
the entrypoint sets `umask 0002`, so new directories on the artefact and export volumes are `2775`
group 0; the object store creates every artefact and export object `0660` less the umask
(`dps/storage.py` `OBJECT_MODE`). So the next UID of group 0 (compose's 10001, an OpenShift
namespace UID, a redeploy under another UID) reads, replaces and adds to what an earlier UID
stored. `ENV HOME=/tmp` (the tmpfs/`emptyDir`), `PYTHONDONTWRITEBYTECODE=1`. Checked on a built
image by `DPS_TEST_IMAGE=data-privacy-services:<tag> pytest -q tests/test_image_runtime.py`
(`DPS_TEST_IMAGE_PLATFORM=linux/amd64` for an amd64 build): uid 1000770000:0, `--read-only`,
`--cap-drop ALL`.

**The browser image** (`Dockerfile.browser`) follows the same rules: user 10001 with primary group
0, `docker/entrypoint.sh` as `ENTRYPOINT` (`umask 0002`; it never migrates for this command), `/app`
group 0 readable and never group-writable, `/app/var` group-writable with setgid directories, `ENV
HOME=/tmp`. `/ms-playwright` stays as the Playwright base ships it (`0777` throughout; a recursive
`chgrp`/`chmod` there copies every browser file into a new layer, measured +1.46 GB), so the
browser-worker **must** run with a read-only root (compose inherits `read_only: true` from
`worker`; OpenShift `readOnlyRootFilesystem: true`). Checked by
`DPS_TEST_BROWSER_IMAGE=data-privacy-services-browser:<tag> pytest -q tests/test_browser_image_runtime.py`.

**Container runtime.** Compose runs every role of the app image (app, worker, migrate,
tokenizer-mock, seed-sources) and the browser-worker with a read-only root, `cap_drop: [ALL]`,
`no-new-privileges` and a `/tmp` tmpfs where the role writes scratch files; the local mocks run the
same way (the demo site as the `node` image's own user, the OCR mock as uid 10001:0). The
exception is the local `demo-secrets` one-shot, which runs as root to write the demo secret files;
the data stores run as their images' own users. On OpenShift keep the image `ENTRYPOINT` (set
`args`, not `command`): it sets `umask 0002`.

Measured sizes (arm64): M1 lane 644 MB (apt layer 107 MB); the integrated M1 + M2 image 925 MB;
`WITH_NER=1` adds ≈ 370 MB. M5 Task 24 (M0–M3 + M5): arm64 1.18 GB unpacked / 286 MB
compressed, `--platform linux/amd64` 292 MB compressed; cold builds 449 s (arm64) and 591 s (amd64
under emulation). `.dockerignore` keeps `.git`, `.venv`, `docs`, `demo`, `tests`, `var`,
every `.env*` and key file out of the build context.

Development install and checks:

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -e ".[dev,fpe,docs,db,connectors,tabular,scanner,uitest,e2e]"   # + ner, vertex, deck as needed
python -m playwright install chromium            # once: Chromium 1187 for the scanner and console checks
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
| `DPS_PATTERN_CPU_SECONDS` | `5.0` |  | M5 | Policy patterns (custom recognizers, deny lists): the most CPU one API request, NDJSON item or job/scan handler call may spend on them in total, and the longest any single run may take; past it 422 `custom_pattern_too_slow` (S3 review F-02). |
| `DPS_PATTERN_PROCESSES` | `2` |  | M5 | Matcher processes per server process for pattern runs that outgrow the 5 ms in-thread slice (single-threaded children, about 17 MB each, started on first use); a run waits at most 30 s for a free one, then 503 `pattern_matcher_busy`. |
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
| `DPS_COIN_CA_FILE` | — |  | M1 | CA bundle (file or directory) for the COIN token mint; else `DPS_SSL_CERT_FILE`, else httpx's default store (`SSL_CERT_FILE`, then `SSL_CERT_DIR` from the environment, else certifi). |
| `DPS_COIN_TLS_INSECURE` | `false` |  | M1 | Skip TLS verification on the COIN mint only (the VDI's intercepting proxy); `/readyz` then reports `coin_tls: insecure`. |
| `DPS_ALLOW_INSECURE_COIN_MINT` | `false` |  | M1 | The documented VDI exception: staging/prod refuse `DPS_COIN_TLS_INSECURE=true` without it. |
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
| `DPS_BROWSER_CONCURRENCY` | `2` |  | M4 | Scans one browser-worker runs at once (1..16), in one Chromium with one isolated context per scan. |
| `DPS_BROWSER_METRICS_PORT` | `9401` |  | M4 | The browser-worker's ops server port (health, readiness, metrics); `0` disables it. |
| `DPS_BROWSER_HEADLESS` | `true` |  | M4 | `false` runs a headed Chromium for local debugging only; refused in staging/prod. |
| `DPS_SCAN_MAX_PAGES` | `50` |  | M4 | Hard page cap per scan (1..500); a scan asking for more is 422 `scope_invalid`. |
| `DPS_SCAN_MAX_DEPTH` | `3` |  | M4 | Crawl depth when a scan omits it (0..5). |
| `DPS_SCAN_PAGE_TIMEOUT_SECONDS` | `30` |  | M4 | One page's navigation and settle time limit. |
| `DPS_SCAN_JOB_TIMEOUT_SECONDS` | `1800` |  | M4 | One scan's time limit. |
| `DPS_SCAN_REGISTERED_HOSTS` | `localhost,127.0.0.1,demo-site` |  | M4 | Local/dev fallback list of the hosts a scan may visit; a tenant's `registered_hosts` (`PATCH /admin/tenants/{id}`) is the list in production. Hosts that are or resolve to link-local, metadata, multicast, reserved or (outside `local`) loopback addresses are refused whatever the list says (`host_forbidden`). |
| `DPS_SCAN_STORE_UNMASKED_ORIGINALS` | `false` |  | M4 | Keep the unmasked original screenshot of a page that renders personal data; must stay `false` in staging/prod. |
| `DPS_SCAN_MAX_PER_TEMPLATE` | `3` |  | M4 | Crawl budget: visits per URL template (`/help/faq/{id}`). |
| `DPS_SCAN_MAX_SPA_CLICKS_PER_PAGE` | `25` |  | M4 | Crawl budget: click-only routes tried per page. |
| `DPS_SCAN_MAX_FIELDS_PER_PAGE` | `400` |  | M4 | Fields inventoried per page. |
| `DPS_SCAN_MAX_SKIPPED_ROWS` | `200` |  | M4 | Skipped-page rows kept for a scan's crawl map. |
| `DPS_SCAN_MAX_EVENTS` | `2000` |  | M4 | Navigation-log rows kept per scan. |
| `DPS_SCAN_SETTLE_MS` | `400` |  | M4 | Wait after a page load or a fill before fields are read or captured (milliseconds). |
| `DPS_SCAN_VIEWPORT_WIDTH` | `1280` |  | M4 | Capture viewport width (CSS pixels). |
| `DPS_SCAN_VIEWPORT_HEIGHT` | `900` |  | M4 | Capture viewport height (CSS pixels). |
| `DPS_SCAN_SCREENSHOT_MAX_HEIGHT` | `12000` |  | M4 | Full-page screenshots are cropped to this height (pixels). |
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
| `DPS_TOKENIZER_PROFILES` | — |  | M5 | Which entity types and data elements each tenant may tokenize through `external_tokenize`: JSON `{"<tenant>\|*": ["SSN:dps-ssn", "ENTITY", …]}`; anything else is refused with 403 `tokenizer_profile_not_allowed` before any call (S3 review F-11). Required with `DPS_TOKENIZER_PROVIDER=http` in staging/prod (app and worker). |
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
1522, `DPS_MONGO_PORT` 27018, `DPS_TOKENIZER_MOCK_PORT` 8420 and `DPS_DEMO_SITE_PORT` 8410; compose also passes through the app settings it names (e.g.
`DPS_JOB_KEK`, `DPS_FPE_ENABLED`, `DPS_LLM_PROVIDER`, the Stellar/COIN variables) with local
defaults. Tests and tools take endpoints only from `DPS_TEST_*` (contract §12.2), whose defaults
follow the same port variables: `DPS_TEST_APP_URL`, `DPS_TEST_PG_DSN`, `DPS_TEST_SOURCE_PG_DSN`,
`DPS_TEST_S3_ENDPOINT`, `DPS_TEST_OCR_URL`, `DPS_TEST_ORACLE_DSN` / `_USER` / `_PASSWORD`,
`DPS_TEST_MONGO_URI`, `DPS_TEST_DEMO_SITE`; `DPS_TEST_REQUIRE_SERVICES=1` turns a missing service
from a skip into a failure (every milestone gate sets it), and with it
`DPS_TEST_REQUIRE_TESSERACT=1` does the same for a missing host `tesseract`
(`tests/documents/test_ocr_tesseract.py`). The tools read `DPS_BASE_URL` / `DPS_TEST_APP_URL` and
`DPS_API_KEY` (default: the local demo key); `tools/smoke_test.py` also `DPS_SMOKE_WAIT_SECONDS`
(90), `DPS_SMOKE_REQUIRE_OCR=1` (fail instead of skipping its OCR checks), `DPS_SMOKE_M2=auto|on|off`,
`DPS_SMOKE_JOB_TIMEOUT` (180 s for each M2 job run), `DPS_SMOKE_M3_TIMEOUT` (600 s for `--m3`),
`DPS_SMOKE_REQUIRE_M5=1` (the M5 block fails instead of skipping), `DPS_SMOKE_KEY_WAIT_SECONDS`,
`DPS_SMOKE_SCANNER=auto|on|off` (the scanner checks; `on` makes a missing scanner a failure),
`DPS_SMOKE_DEMO_URL` (default `http://demo-site:8410`, the demo site as the browser-worker sees
it) and `DPS_SMOKE_SCAN_TIMEOUT` (300 s);
the console checks `DPS_SHOTS_DIR` (screenshot root, default the git-ignored `reports/console/`)
and `DPS_PLAYWRIGHT_CHROMIUM` (a Chromium binary when Playwright's build 1187 is not cached);
`tools/scratch_pg.sh` `DPS_SCRATCH_DIR`.

### 3.3 Production posture (the boot guard)

`DPS_ENV=staging|prod` runs `dps.posture` before the app is built, and the worker (`python -m
dps.worker`) runs the same guard at start, before it opens the database; a violation stops the
process, naming the setting, never its value. A staging/prod worker therefore needs the settings
and mounted secrets below, `DPS_API_KEYS_FILE` included, although it serves no API (§5). The browser-worker (`python -m dps.browser_worker`) runs the guard as well, as its own role,
without the rules of components it never runs and whose secrets it does not mount —
`DPS_API_KEYS`, `DPS_CORS_ORIGINS`, the OCR provider and key, `DPS_TOKENIZER_TOKEN_FILE` and
`DPS_TOKENIZER_PROFILES` — so its deployment shares the app's ConfigMap without those mounts. It
requires:

- auth on, `DPS_BOOTSTRAP_API_KEY` unset, `DPS_API_KEYS` set (from `_FILE`; the file may hold `[]`
  once every key is DB-backed), `DPS_LLM_PROVIDER` not
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
  and, when any is set, `DPS_SSL_CERT_FILE`; `DPS_TOKENIZER_TOKEN_FILE` and `DPS_TOKENIZER_PROFILES`
  (S3 review F-11) with `DPS_TOKENIZER_PROVIDER=http`; `DPS_CORS_ORIGINS` without `*`;
- **strict verification at boot:** `DPS_VERIFY_WARN_ALLOWED=false`, `DPS_LLM_EGRESS_CHECK=strict`,
  and every shipped preset and `DPS_DEFAULT_POLICY` with `verify.leakage_check: strict`. At request
  time a policy (inline, preset override or stored) with `leakage_check: warn`, or a run with
  `options.verify=warn`, is then 422 `verify_warn_disallowed`, and publishing such a tenant policy
  is refused the same way;
- always, database or not: `DPS_SCAN_STORE_UNMASKED_ORIGINALS=false` and `DPS_BROWSER_HEADLESS=true`
  (M4).

In **every** environment, not only staging/prod, the app's RLS posture check refuses to boot while
the API login holds an M5 `owner_directory` policy (`USING (true)`, which reads every tenant's
jobs, connections and policies): migrations run as the API login — an unset
`DPS_PG_MIGRATION_USER` falls back to it — would give it one (S3 review F-06). Always set
`DPS_PG_MIGRATION_USER` (compose: `dps_owner`).

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
| `scanner` | `browser-worker`, `demo-site` |
| `full` | every service above |

| Service | Image | Host port → container | Notes |
|---|---|---|---|
| `app` | `data-privacy-services:${DPS_IMAGE_TAG:-local}` (build `.`) | `${DPS_APP_PORT:-8400}` → 8400 | console + API; `DPS_ENV=local`, the bootstrap key, `DPS_DEFAULT_LOCALE=en_IN`, `DPS_FPE_ENABLED=true`, `DPS_LLM_PROVIDER=stub`, OCR via the mock; `depends_on` dps-db, migrate and (optionally, `required: false`) the OCR mock; read-only root, 256 MB `/tmp` tmpfs (uploads over 1 MiB spool there), `cap_drop: [ALL]`, `no-new-privileges`, `mem_limit: 1536m` |
| `dps-db` | `postgres:16-alpine` | `${DPS_DB_PORT:-5441}` → 5432 | metadata database; `docker/dps-db/initdb/01_roles.sql` creates the roles of §6 (local passwords = role name) and the `dps` database (owner `dps_owner`, timezone UTC); volume `dps-db-data`; superuser `postgres`/`postgres` (local only) |
| `migrate` | the app image | — | one-shot `python -m dps.migrate` as `dps_owner` (`DPS_MIGRATIONS_MODE=auto`), exits 0; app and worker wait for it and run with `verify` |
| `worker` | the app image | — (9400 inside the network) | `python -m dps.worker`, `DPS_WORKER_CONCURRENCY=4`; scale with `--scale worker=N`; 512 MB `/tmp` tmpfs (files targets spool there), `mem_limit: 1g` |
| `azure-ocr-mock` | `document_intelligence-azure-ocr-mock:${DPS_IMAGE_TAG:-latest}` (build `../mock_azure_ocr`) | `${DPS_OCR_PORT:-5016}` → 5000 | the Azure Computer Vision Read v3.2 contract with Tesseract inside; the app calls it as `http://azure-ocr-mock:5000` with the local key `mock-key`; runs as uid 10001:0 with a read-only root, `cap_drop: [ALL]`, `no-new-privileges` and a `/tmp` tmpfs |
| `source-pg` | `postgres:16` | `${DPS_SOURCE_PG_PORT:-5442}` → 5432 | demo source: `docker/source-pg/initdb/01_bank_schema.sql` creates the `bank` database (schema `bank`, 10 tables; target schemas `bank_uat`, `bank_restore`, `bank_perf`) and the logins `dps_reader` (SELECT on `bank`) and `dps_writer` (owns the target schemas) |
| `minio` / `minio-init` | `minio/minio:latest` / `minio/mc:latest` | `${DPS_MINIO_PORT:-9010}` → 9000, `${DPS_MINIO_CONSOLE_PORT:-9011}` → 9001 | artefacts and `files` exports; the one-shot creates the buckets `dps-artefacts` and `dps-exports`; root user `minioadmin`/`minioadmin` (local only) |
| `demo-secrets` | the app image (root) | — | one-shot: writes the demo `secret_ref` files under the demo tenant's folder `default/` of the `dps-secrets` volume (app and worker mount it read-only at `/var/run/secrets/dps`): `dps/src/bank-pg-ro`, `dps/tgt/bank-uat`, `dps/src/bank-restore-rw`, `dps/s3/exports`, the Oracle refs `dps/src/oracle-crm-ro`, `dps/src/oracle-crm-owner`, `dps/tgt/oracle-crm-masked`, `dps/src/oracle-loans-owner` (`username`, `password`, `dsn`) and the MongoDB refs `dps/src/mongo-crm`, `dps/tgt/mongo-crm-uat` (`uri`); removes an older volume's top-level `dps/` folder |
| `seed-sources` | the app image | — | one-shot: `python tools/seed_sources.py --target postgres --rows 20000 --seed 7 --restore-copy` (idempotent; the first run takes about a minute) |
| `oracle` | `gvenzl/oracle-free:23-slim-faststart` | `${DPS_ORACLE_PORT:-1522}` → 1521 | service `FREEPDB1`; `docker/oracle/initdb/01_crm.sql` runs once per fresh container as SYS: users `CRM` (`CUSTOMERS`, `INTERACTIONS`, `KYC_DOCUMENTS`, `ADDRESS_HISTORY`, `CUSTOMER_EVENTS`), `CRM_MASKED` (target), `LOANS` (`APPLICANTS`), `DPS_RO` (CREATE SESSION + SELECT on those six tables); passwords = user name, SYSTEM `dps_local_oracle` (local only); unhealthy until the init script finished (1–3 min); `mem_limit: 2700m`; no volume (`down` resets it) |
| `mongo` | `mongo:7` | `${DPS_MONGO_PORT:-27018}` → 27017 | one-member replica set `rs0` (`--replSet rs0 --bind_ip_all`; the healthcheck initiates it and is healthy only as a writable primary); clients use `directConnection=true`; no auth (local only); `mem_limit: 1g` |
| `tokenizer-mock` | the app image | `${DPS_TOKENIZER_MOCK_PORT:-8420}` → 8420 | vaultless mock of the bank tokenization service (`docker/tokenizer-mock/server.py`, mounted read-only): tokens `tk_` + base32(AES-SIV); bearer token `dps-local-tokenizer-token` (local demo literal); app and worker point `DPS_TOKENIZER_URL` at it; without it `/readyz` reports `tokenizer` down and `external_tokenize` answers 502 |
| `browser-worker` | `data-privacy-services-browser:${DPS_IMAGE_TAG:-local}` (build `Dockerfile.browser`) | — (9401 inside the network) | the scanner's worker: `python -m dps.browser_worker`, `DPS_BROWSER_CONCURRENCY` scans in one Chromium (one isolated context per scan); `init: true`; read-only root inherited from `worker`, `/tmp` tmpfs (Chromium's profile and `HOME`) |
| `demo-site` | `node:22-slim` (`docker/demo-site`) | `${DPS_DEMO_SITE_PORT:-8410}` → 8410 | the demo onboarding site the scanner crawls (in-network `http://demo-site:8410`), as the `node` image's user with a read-only root; `GET /__received` counts every non-GET request and form submission it ever received — it never moves during a scan (D4) |

Every long-running service has a healthcheck and every service a `mem_limit`; the one-shots built
on the app image inherit its `HEALTHCHECK` and show "unhealthy" after exiting 0 (harmless). Labels
`app=data-privacy-services`, `component=<role>`, `env=local`. Compose environment shared by app, migrate and worker
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
`dps-artefacts` volume.) Under `full` every role of the app image gets
`DPS_SYNTH_TABULAR_ENGINE=copula`, `DPS_KEY_CACHE_SECONDS=5` (a key minted through `/admin/keys`
reaches every replica within 5 s; the production default stays 30) and
`DPS_REPORT_PDF_ENABLED=true` (each overridable from the shell), and the app starts after the
tokenizer mock is healthy (`depends_on … required: false`, like the OCR mock) because `/readyz`
probes the tokenizer once at startup. The release checks of the whole stack:
`DPS_SMOKE_REQUIRE_OCR=1 DPS_SMOKE_REQUIRE_M5=1 DPS_SMOKE_SCANNER=on python tools/smoke_test.py
--m3` (after the three-engine seed), `python tools/flow_report.py --require-scanner` (writes the
git-ignored `reports/flow-report.md`) and the ten-minute demo `python tools/demo_seed.py && python
tools/e2e_demo.py --console` (record: §14.8). Never use `up --wait`: it fails on the one-shots that exit 0 — poll
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
Ranges: M1–M2 001–019 (001–010 used), M3 020–029 (020), M4 030–039 (030–031), M5 040–059
(040–046; `046_job_group_kcv.sql` stores a job group's key check value, so a group run must carry
the group's DEK, not only its kid — S3 review F-13). Applied
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

| Kubernetes object (as `deploy/openshift/base` mounts it) | Feeds | Read by |
|---|---|---|
| Secret `dps-pg-app` | `DPS_PG_PASSWORD_FILE` (the API login, `DPS_PG_USER`) | `dps-app`; `dps-worker` and `dps-browser-worker`, which open this pool at start as well as their own; the operator's `dps.keys_cli` and `dps.rekey` runs |
| Secret `dps-pg-worker` | `DPS_PG_WORKER_PASSWORD_FILE` | `dps-worker`, `dps-browser-worker`, the operator's `dps.rekey` run |
| Secret `dps-pg-owner` | `DPS_PG_MIGRATION_PASSWORD_FILE` | the `dps-migrate` Job and the daily `dps-migrate-maintain` CronJob, the same pod; nothing else |
| Secret `dps-job-kek` | `DPS_JOB_KEK_FILE` (and `DPS_JOB_KEK_PREVIOUS_FILE` during a rotation) | app, worker, `dps-browser-worker` (it does not start without it), the operator's `dps.rekey` run |
| Secret `dps-api-keys` | `DPS_API_KEYS_FILE` (static keys). **Required at boot in staging/prod** by the posture guard of the app and of the worker (§3.3); once every key is DB-backed it may hold `[]` | app, worker; the manifests mount it in `dps-browser-worker` too, whose guard does not require it |
| Secret `dps-coin` | `DPS_COIN_CLIENT_ID_FILE`, `DPS_COIN_CLIENT_SECRET_FILE`, `DPS_COIN_SCOPE_FILE` | app, worker |
| Secret `dps-tokenizer` | `DPS_TOKENIZER_TOKEN_FILE` | app, worker |
| Secret `dps-ocr` | `DPS_AZURE_VISION_KEY_FILE` | app, worker |
| Secret `dps-s3` | `DPS_S3_ACCESS_KEY_ID_FILE`, `DPS_S3_SECRET_ACCESS_KEY_FILE` | app, worker, `dps-browser-worker` (scan artefacts) |
| Secret `dps-connector-refs` | the connection `secret_ref` folders under `DPS_SECRET_DIR` | app, worker, browser-worker |
| ConfigMap `dps-ca-bundle` | `DPS_SSL_CERT_FILE`, `DPS_PG_SSL_ROOT_CERT` (and optionally `DPS_COIN_CA_FILE`, `DPS_TOKENIZER_CA_FILE`) | every role |

The browser worker runs with `DPS_LLM_PROVIDER=""`, `DPS_OCR_PROVIDER=off` and
`DPS_TOKENIZER_PROVIDER=""` and mounts none of their secrets (a policy's `llm_label` tier then
labels no scanned field; with a provider configured the browser-worker needs the app's LLM settings
and secrets, §14.5.1). Every secret is a `0440` file read through the SCC-assigned `fsGroup`.

`DPS_BOOTSTRAP_API_KEY` is a local demo literal and is never set outside a laptop. A KEK
(`dps-job-kek`) is base64 of exactly 32 random bytes (any other length is refused at start):

```bash
python -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())'
```

**Connection credentials** (`dps/connections.py`, SEC-2). A connection's `secret_ref` names a folder
under the caller's tenant folder, `DPS_SECRET_DIR/<tenant_id>/<ref>/` (the tenant comes from the
authenticated request, never from the reference), from which only these files are read:
`username`, `password`, `dsn` (Oracle), `uri` (MongoDB), `access_key_id` + `secret_access_key`
(S3), `storage_state.json` (a scan's session, M4) and `ca.pem` (a source's CA bundle;
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
(stored `secret_ref` values do not change). A scan's `auth.storage_state_ref` follows the same
rules (SEC-5: `POST /scans` refuses another tenant's folder with 403 `secret_ref_foreign_tenant` and
an unlisted shared one with 403 `secret_ref_not_shared`; the browser-worker resolves it with
`dps.secrets.resolve_secret_ref(tenant_id, ref, settings)` and fails the scan closed with
`secret_unavailable` otherwise); refs follow `dps/ui/<name>` (the local demo session
`dps/ui/demo-onboarding` is a committed demo literal for the demo site only). The browser-worker
needs `DPS_JOB_KEK`: it opens the scan's sealed key, seed, start URL, policy body and any inline
dev storage state, and purges them at the end.

**Who may change a connection (SEC-3).** Any `PATCH` or `DELETE` of a connection that is or becomes
tagged `production` needs the `admin` scope (403 `admin_scope_required`). On a connection holding
credentials, changing a param that decides where they are sent needs the credentials restated in
the same request (or `admin`), else 403 `connection_endpoint_change_requires_credentials`; the list
is `dps.connections.ENDPOINT_PARAMS` (postgres `host port database dbname options.sslmode
options.sslrootcert_ref`; oracle `host port service_name sid dsn` + `options.protocol` and the TLS
options; mongo `uri host port replica_set database options.replicaSet options.directConnection
options.tls`; s3 `endpoint_url bucket region`; pvc none), and `tests/test_connection_endpoints.py`
fails until a new `ConnectionParams` field is classified. **Production credentials** (S3 review
F-01, round 1 B2): a `secret_ref` that a `production`-tagged connection holds is a production
credential — naming it in another connection (create or patch) needs `admin`, and a connection that
already holds it is changed or deleted like the tagged one (any `PATCH`, any `DELETE`: `admin`).
Residual: a `jobs` key may still *create* a connection naming any of its own tenant's
**non-production** refs with an endpoint of its choice — restrict the key or mount fewer refs per
tenant.

**Who may write a production connection (D3).** **Every** run that writes a production connection —
tagged `production`, or holding a production credential (`dps.connections.production_refs`) — needs
the `admin` scope: `in_place` and `append` (which also need `confirm_write: true`), and equally a
`table` load (`truncate_insert` empties an existing table or collection, `upsert` rewrites it, a new
table is created in production) and a `files` export into a production `s3`/`pvc` connection;
otherwise 403 `admin_scope_required` from `POST /jobs/run/validate` and `POST /jobs/run`, and no job
is created (S1-M3 post-merge r1 security B1 widened the rule to every write mode; the S3 review bound
it to the credentials; the release merge joined both). Reading a production *source* into a
non-production target stays a `jobs` right. `POST /jobs/{id}/retry` re-checks the same rule against
the job's written connection (its target, else its source) as it is now, before any key check, so a
`jobs` key cannot restart an admin's failed production run.

**Bootstrap of the first admin key** (staging/prod, DB-backed keys). With the metadata database's
API login (`dps-pg-app`) in the environment (any pod with the image): `python -m dps.keys_cli
create --name platform-admin --tenants '*' --scopes '*' --expires-in-days 90` prints one JSON
document with the raw key, once; store it in the secret manager and leave `DPS_BOOTSTRAP_API_KEY`
unset. `list`, `rotate ID [--grace-hours H]`, `disable ID` and `enable ID` manage keys the same way
(exit 2 with `{"error", "detail"}` on a refusal); every mutating command writes an audit row owned
by `_system`.

## 6. Services and dependencies

| Dependency | Used by | Contract |
|---|---|---|
| Metadata PostgreSQL 16 (external in production; `dps-db` locally) | app, worker, browser-worker, migrate | schema `DPS_PG_SCHEMA` with tenant row-level security; roles below; `python -m dps.migrate` as `dps_owner`; verified TLS with `DPS_PG_SSL_ROOT_CERT` |
| Object storage (S3; MinIO locally) or a volume | app (artefacts, evidence PDFs), worker (`files` targets, artefacts) | `DPS_ARTEFACT_STORE=s3`: bucket `DPS_S3_BUCKET`, keys `t/<tenant>/<job>/<artefact>.<ext>`; `files` targets write `<prefix>/<job>/part-NNNNN-NNN.<ext>` + `manifest.json` (names never carry a value) |
| Stellar LLM gateway + COIN token mint | app, worker | `<base>/chat/completions` with a COIN OAuth2 token; **typed placeholders only** (P1): every prompt passes the egress verifier (`DPS_LLM_EGRESS_CHECK`) |
| OCR gateway (Azure Computer Vision Read v3.2 contract) or local Tesseract | app (documents) | `DPS_AZURE_VISION_ENDPOINT` + key, health path `/_gw/health` behind the org gateway; page images only (DPS rasterises PDFs) |
| Bank tokenization service | app (`external_tokenize`, reveal), worker | §8 |
| Source and target databases: PostgreSQL, Oracle (thin, 19c/23ai), MongoDB (replica set for `in_place`/`append`) | worker (reads, writes), app (connection tests, describe, count) | per-connection `secret_ref`; read-only transactions on sources; least-privilege checks in `POST /connections/{id}/test` |
| Scan targets (registered UAT hosts) | browser-worker | GET/HEAD only; nothing is ever submitted (D4); Chromium resolves no name itself (`--host-resolver-rules`): the scan's scope proxy resolves every hop and dials only addresses that pass the SSRF rule (S5, §9) |

Metadata database roles (`docker/dps-db/initdb/01_roles.sql` locally; platform-created in
production): `dps_owner` (owns the schema, runs migrations), `dps_app_rw` and `dps_worker` (NOLOGIN
privilege roles, no BYPASSRLS), `dps_app` (the API login, member of `dps_app_rw`) and
`dps_worker_login` (the worker login, member of both). The app role has no DELETE on API keys
(keys are disabled, never deleted).

Who talks to whom: `dps-app` → metadata DB, object storage, LLM gateway, OCR gateway, tokenization
service, source/target databases (tests, describe, count); `dps-worker` → metadata DB, object
storage, LLM gateway (when a job's policy uses it; placeholders only), OCR gateway, tokenization
service, source and target databases, the export volume; `dps-browser-worker` → metadata DB, object
storage, the registered scan hosts; `dps-migrate` → metadata DB only. Nothing calls DPS except API
clients and the console.

**Database specifics worth knowing.**

- PostgreSQL sources: every read runs in `BEGIN READ ONLY` with `statement_timeout`,
  `lock_timeout=5s` and `application_name = dps:<action>:<job id>`; sessions are reset on release
  (`pg_advisory_unlock_all()`, `RESET ALL`); binds are coerced by the prepared statement's types.
  The connection test's advisory `no_sql_string_functions` counts the SQL-string and export
  functions the source login can execute (`dblink*`, `crosstab*`, `connectby`, `xpath_table`,
  `pg_background_launch`, `query_export_to_s3`): give the source login none.
  `in_place`/`append` targets keep a write ledger `dps_write_ledger` in the target schema, or in
  the schema `target.ledger_owner` names (Oracle too); the target login needs `CREATE` there, or a
  pre-created ledger with `SELECT`, `INSERT`, `UPDATE` and `DELETE`; a failed job keeps its ledger
  rows for the resume.
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

`deploy/openshift/` in the Wega layout — base, components (`network-policies`: default-deny plus
one allow per path; `monitoring`: PodMonitor + PrometheusRule; `dev-mocks`) and the `dev`/`prod`
overlays with release placeholders (zero digests, `.invalid` hosts, TEST-NET-1 `192.0.2.0/24` peers,
the Job name `dps-migrate-0`) and a `release-example/`; `deploy/openshift/README.md` is the full
guide with the steps and the parameter table. As built (record, proofs and the runtime emulation:
§14.7):

- One image, four workloads: Deployment `dps-app` (API + console, port 8400, Route with edge TLS),
  Deployment `dps-worker` (ops 9400, the `dps-exports` PVC at `DPS_EXPORT_ROOT`), Deployment
  `dps-browser-worker` (image `data-privacy-services-browser`, ops 9401, `/dev/shm` in memory), and
  the Job `dps-migrate-<release>` (it runs with `DPS_MIGRATIONS_MODE=auto`; it and the daily CronJob `dps-migrate-maintain` running `python -m dps.migrate --maintain` in the
  same pod are the only workloads that mount `dps-pg-owner`). ServiceAccounts `dps-app`,
  `dps-worker`, `dps-browser-worker`, `dps-migrate`, all without a mounted token.
- The metadata database is the platform's PostgreSQL 16 behind the ExternalName Service `dps-db`;
  Oracle Free, MongoDB, MinIO and the source PostgreSQL are never deployed there (bank systems).
- Every pod `runAsNonRoot`, seccomp `RuntimeDefault`, no `runAsUser`; every container without
  privilege escalation, read-only root filesystem, all capabilities dropped, requests and limits;
  every secret a mounted file (§5); images pinned by digest.
- Each Deployment starts with the init container `wait-for-schema` (its own image, `python -m
  dps.migrate --verify` every 5 s until the release's schema is there; a trap ends it at once on
  SIGTERM): the app verifies the schema once at boot, so without it an app pod that started before
  the release's Job committed kept every database route at 503. A release that applied a migration
  is never rolled back by image (fix forward).
- `DPS_PG_HOST` is the platform host itself (equal to `dps-db`'s externalName): verify-full TLS checks
  the certificate against the name dialled. The ConfigMap sets `AWS_CA_BUNDLE` to `org-ca.pem` (boto3
  reads neither `SSL_CERT_FILE` nor `DPS_SSL_CERT_FILE`). No service links (Services named `dps-*`
  would inject `DPS_*` variables); `HOME=/tmp` in the pod env.
- Evidence packs carry `DPS_IMAGE_DIGEST`: the ConfigMap holds the app image's digest and the browser
  worker sets its own; the release overlay derives both from `images:` with `replacements`.
- The CronJob `dps-migrate-maintain` runs `python -m dps.migrate --maintain` daily at 03:17 UTC: the
  audit log has one partition per UTC month and no default partition, so without it every audit
  write fails about four months after a release (alert `DpsAuditMaintenanceStale`).
- `python tools/openshift_check.py <dir>` warns on every placeholder and denies hardening
  violations, a DPS container without its schema wait, a wrong image digest setting, a
  `DPS_PG_HOST` that is not `dps-db`'s externalName, literal secrets in a ConfigMap and any role
  whose settings fail `Settings` or the posture guard; `--fail-on-warn` is the release gate. Every
  JSON6902 `replace` is preceded by a `test` of the committed value.
- Platform prerequisites: user-workload monitoring; ConfigMap `dps-ca-bundle` (`pg-root.pem`,
  `org-ca.pem`); the RWX PVC `dps-exports`; the secrets of §5; network paths for every peer;
  connector `secret_ref` folders from `dps-connector-refs` (or a CSI/Vault mount) at `DPS_SECRET_DIR`.
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
`external_tokenize` tokens bound under it (§8). While `DPS_JOB_KEK_PREVIOUS(_FILE)` is mounted, the
app and workers open blobs sealed under the old KEK too (job keys and specs, inline connection
credentials) and an `Idempotency-Key` admitted under it still replays, on the job routes and `POST
/scans` (S3 review F-21) — but a pod that holds only the new KEK cannot: run `dps.rekey` right after
the first roll and before removing the previous KEK.

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
A scan of any other host is refused (`422 host_not_registered`; the local fallback list is
`DPS_SCAN_REGISTERED_HOSTS`), and so is a host that is — or resolves to — a link-local/cloud-metadata,
multicast, unspecified or reserved address, a numeric host encoding, or loopback outside
`DPS_ENV=local` (`422 host_forbidden`, S5; RFC 1918 stays allowed): the **app** must resolve the
tenant's registered hosts, and the browser-worker checks the start URL again before a scan runs. In
OpenShift also widen the `egress-scan-targets` NetworkPolicy parameter (§7).

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
  and the KEK is loaded), `keys` (`static` or `db+static`), `browser` (`N online` when a browser-worker heartbeat is
  recent, refreshed every 15 s; `unknown` for the first seconds after start, `none online`
  without one). 503 only when a required component is down.
- Worker ops server (`DPS_WORKER_METRICS_PORT`, 9400): `/health`, `/readyz` (`db` = `SELECT 1`,
  `migrations` verified, `kek` loaded, `queue` claiming) and `/metrics`. SIGTERM drains: stop
  claiming, wait `DPS_JOB_DRAIN_TIMEOUT_SECONDS` while heartbeats keep the leases, release the rest
  without charging an attempt, exit 0.
- Browser-worker ops server (`DPS_BROWSER_METRICS_PORT`, 9401; 0 = off): `/health`, `/readyz`
  (Chromium up — `browser: chromium <version>` — and the consumer claiming) and `/metrics` with
  `dps_scan_pages_total{outcome}`, `dps_scan_requests_total{decision,method,reason}`,
  `dps_scan_fields_total{entity_type}` (custom types labelled `custom`) and
  `dps_scan_fills_total{mode,outcome}` — labels never carry a URL or a value. Same SIGTERM drain as
  the worker; the scan audit rows (`scan.navigate` per page, `scan.finalize`) carry page numbers,
  durations and counts only.
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
- Alerts (`deploy/openshift/components/monitoring/prometheusrule.yaml`): `DpsRawPiiSentToLlm` (critical, any increase of either
  raw-PII counter — must never fire), `DpsLlmExposureBlocked` (warning: the egress verifier stopped
  a prompt), plus `DpsVerificationFailures`, `DpsExactRowResamplesHigh`, `DpsTokenizerFailing`,
  `DpsNoDbKeysLoaded`, `DpsWorkersAbsent`, and `DpsAuditMaintenanceStale` (warning: the daily
  `dps-migrate-maintain` run has not succeeded for 3 days, or failed and never succeeded).
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
- Kustomize generator/component ordering: inside one kustomization it runs the
  resources, then its own generators, then its components, then its patches, so an overlay's
  `configMapGenerator` `behavior: merge` cannot reach a ConfigMap a component generates (set such
  a value with a patch in that overlay, or merge from a parent, e.g. the release overlay).
- The copied dev mock: kustomize loads files only from inside a kustomization root,
  so `deploy/openshift/components/dev-mocks/server.py` is a byte-identical copy of
  `docker/tokenizer-mock/server.py` (`tests/deploy/test_openshift.py` keeps them equal): change
  both.
- Volumes written by an image older than the arbitrary-UID change hold `10001:10001 0755`
  directories, and objects stored before the `fimg` review fix are `0600`: another UID then fails
  with `storage_error`. Re-permission once — `docker compose run --rm --no-deps --user 0 --cap-add
  CHOWN --cap-add FOWNER --entrypoint sh worker -c 'chgrp -R 0 /app/var/artefacts /app/var/exports
  && chmod -R g+rwX /app/var/artefacts /app/var/exports && find /app/var/artefacts /app/var/exports
  -type d -exec chmod g+s {} +'` — or `down -v` locally.
- `docker compose exec` does not pass through the entrypoint (its shells show umask `0022`); the
  services' own processes run with `0002`.
- `.gitignore` anchors `/reports/` (tool reports and screenshots at the repository root): an
  unanchored `reports/` would silently ignore new files of the package `dps/layers/reports/`.

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
  (`verification.coverage`) — except a row's **own** replaced name: an L2 run treats the row's
  planned name, and each of its words, as a span wherever the row's free text repeats it (whole
  words, any case), and the same for any other replaced whole value (a city), from two characters
  up, longest first. Names of *other* people in free text still need NER.
- **Policy patterns are bounded per call, on their own CPU** (S3 review F-02, rounds 1–3; S3c).
  Custom-recognizer patterns and deny lists run on the `regex` package (`VERSION0`, with a matching
  timeout), never on `re`; a run starts in the calling thread for at most 5 ms and continues in a
  matcher process (`DPS_PATTERN_PROCESSES` single-threaded children, `python -I`, empty
  environment, started on first use, replaced if they die — the run is then refused). Every call —
  an HTTP request, each NDJSON item, a worker or browser-worker handler task — gets a budget (10 ms
  plus 20 µs per pattern run and 0.5 µs per character per pattern scanned), at most
  `DPS_PATTERN_CPU_SECONDS` (5 s); past it the call is refused with 422 `custom_pattern_too_slow`
  (a job, partition or scan fails with that code and is not retried). No policy pattern runs, and no
  deny list is built, on the event loop serving a request (a run there is refused as a server
  defect, `pattern_on_event_loop`); a run waits at most 30 s for a matcher process, waiting runs
  served in arrival order (503 `pattern_matcher_busy`, retryable; a failed matcher answers 422).
  Deny lists are matched as a trie (terms at most 256 characters), their build charged to the call.
  The static filter (`check_pattern`: no nested repeats, backreferences, bounds over 1000, patterns
  over 512 characters, no unescaped `{` that is not a repeat count, no unescaped `[` inside a set)
  still refuses the classic shapes when a policy is validated. A bank recognizer that is quadratic
  on long tokens (`\w+@\w+` on a base64 blob) can be refused on large inputs — rewrite it with
  bounded repeats. `regex` differs from `re` in a few Unicode details (security review §5).
- **Custom entity tiers are per call** (S3 review F-10): the tiers a policy declares live in the
  request's or job handler's own registry (`dps.engine.call_scope`), never in a process-wide one.
  Any new entry point that runs the engine must run inside an HTTP request or a `Consumer` handler
  task (both bind the scope).
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
  discovery does not classify nested address fields (map them in review); a `files` target writes
  a field holding a sub-document or an array as one column of JSON text (manifest type
  `document`; nested leaves: datetime ISO-8601, Decimal128 digits, ObjectId hex, Int64 int, bytes
  hex).
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
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m browser                      # M4
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m deploy                       # Task 25 (kustomize)
DPS_SMOKE_REQUIRE_OCR=1 DPS_SMOKE_REQUIRE_M5=1 DPS_SMOKE_SCANNER=on python tools/smoke_test.py --m3   # every block, no SKIP
python tools/flow_report.py --require-scanner                        # Task 24
python tools/demo_seed.py && python tools/e2e_demo.py --console      # Task 26, under 10:00
pytest -q tests/test_no_values_in_logs_m1.py tests/test_no_values_in_logs_m2.py tests/test_no_values_in_logs_m3.py
pytest -q tests/test_no_values_in_logs_m4.py                         # M4
# DoD 9: the security tests and the M5 log scan, and
# docs/security/2026-10-11-m5-security-review.md complete with every row's status
DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/security tests/test_no_values_in_logs_m5.py
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

## 14. Records of the merges into `main`

The build records of the merges §1–§13 summarise, kept as they were written (main's HANDOFF §14–§21
before the release merge, renumbered; their "§N" references to other base sections point at the
pre-release layout — the facts now live in §1–§13). Each record says what changed for a build, a
deploy or a demo, how it was verified, and the traps found; per-task details are in
`docs/superpowers/build/*-DEVIATIONS*.md` and the reports under `~/.cache/dps-build/reports/`.

### 14.1 M3 integrated on `main` (2026-10-14, WP-I)

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
- Post-merge verification r1 fixes (2026-10-14): a key-less Oracle table synthesizes (the D-A prepare
  guard no longer counts the `ROWID` pseudo key as a copied key; no file or table ever gets a `ROWID`
  column); a name's words are row-value spans too, in both modes, so the CRM notes' first name never
  reaches a target; MongoDB → `files` works in every format (document fields are JSON text, nested
  BSON leaves converted); Oracle `TIMESTAMP(7-9)` / `WITH TIME ZONE` are exact Parquet strings; every
  write into a production-tagged connection needs `admin` (above); a MongoDB evidence pack counts
  each nested leaf under its path, and the console's Evidence page labels leaves by path.
- Post-merge verification r2 fixes (2026-10-14): row-value spans have their own floor of two
  characters (r1 searched name words from 3 letters and values from 4: 28 two-letter first names and
  34 three-letter cities of the 20 000 demo customers stayed in synthetic notes) and match longest
  first; finalize clears the write ledger of a key-less Oracle `table` load into a table it created
  on a separate target connection (`CRM` → `CRM_MASKED`: it described the source on the target login,
  failed silently and left one ledger row per job with `write_ledger_cleared: false`). Ledger rows
  left by runs before this fix stay in `CRM_MASKED.DPS_WRITE_LEDGER` until deleted by job id. Test
  oracles compare a format-preserving id row by row only (an FF1 id may equal *another* customer's id;
  the M2 gate's MongoDB pipeline test went red on that about 1.4 % of runs).
- Tests: the migration test checks each milestone range and unique numbers; an address-line leaf keeps
  main's own `line1` cue under an e-mail parent.

**Traps.** The M3 integration tests reseed the shared demo `CRM.*` and `crm.profiles` at test sizes
(2 400 rows); reseed with `tools/seed_sources.py --target all --rows 20000 --seed 7` before a demo or
a source-vs-target count. Console tests overwrite the tracked screenshots under
`docs/superpowers/build/m3-shots/` (A-N2). Start main with `DPS_TOKENIZER_MOCK_PORT=8430` while the
M4 lane holds 8420. Gates and results: `~/.cache/dps-build/reports/I-m3.md`.

### 14.2 M4 — UI scanner: browser-worker image and the `scanner` profile (lane `dps-wt-m4`)

What M4 adds to the stack (contract §3.5, §11, §11.1): the **browser-worker** role (`python -m
dps.browser_worker`, claims `scan` jobs only) on its own image, and the **demo-site** (the
onboarding web app every scanner test, the smoke test and the demo scan). The app needs no change: it
validates scan hosts against the same default `DPS_SCAN_REGISTERED_HOSTS`
(`localhost,127.0.0.1,demo-site`) and serves the screenshots from the artefact store it shares with
both workers.

**Image `Dockerfile.browser`** → `data-privacy-services-browser:${DPS_IMAGE_TAG:-local}`:
`FROM mcr.microsoft.com/playwright:v1.55.0-noble`, whose `/ms-playwright` holds Chromium build 1187 —
exactly what `playwright==1.55.0` drives, so the image never runs `playwright install`
(`PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`; the build asserts the installed version is 1.55.0).
`uv` 0.11.21 (pinned, as the app image) creates `/opt/venv` from the base's `/usr/bin/python3.12`
(Ubuntu 24.04 has no `ensurepip`) and installs `.[fpe,db,connectors,scanner]` (`scanner` =
`playwright==1.55.0` + Pillow; `WITH_NER=1` adds `ner` + `en_core_web_sm`); no `docs`/`tabular`
and no console. It runs as **uid 10001** (`appuser`, as the app image — the base's `pwuser` could not
write the shared `dps-artefacts`/`dps-exports` volumes, which the app image creates owned by 10001),
`EXPOSE 9401`, a `urllib` `HEALTHCHECK` on `http://localhost:9401/health` (start period 30 s), `CMD
["python", "-m", "dps.browser_worker"]`. Size (arm64, 2026-10-13): 4.22 GB, almost all of it the
Playwright base (3.53 GB: Chromium, Firefox, WebKit and their system libraries).

**Compose (`--profile scanner`, also in `full`):**

| Service | Image | Host port | Notes |
|---|---|---|---|
| `browser-worker` | `data-privacy-services-browser:${DPS_IMAGE_TAG:-local}` | none (ops `/health` `/readyz` `/metrics` on 9401 inside the network) | `extends: worker` — the same database, `DPS_JOB_KEK`, artefact store, volumes and hardening (read-only root, `/tmp` tmpfs 512 MiB, `cap_drop: ALL`, `no-new-privileges`); overrides: `DPS_MIGRATIONS_MODE=off` (the `migrate` one-shot owns DDL), `DPS_BROWSER_CONCURRENCY=2`, `DPS_BROWSER_METRICS_PORT=9401`, `DPS_SCAN_REGISTERED_HOSTS=localhost,127.0.0.1,demo-site`, `DPS_FPE_ENABLED` (must equal the app's); secrets: the worker's tenant-scoped `dps-secrets` volume (`DPS_SECRET_DIR=/var/run/secrets/dps`, read-only — the same tree the app checks a `storage_state_ref` against at accept, SEC-2/SEC-5), after `demo-secrets` has run; `shm_size: 1gb`, `init: true`, `mem_limit: 2g` |
| `demo-site` | `node:22-slim` running `demo/onboarding/server.mjs` (bind-mounted read-only; no build) | `127.0.0.1:${DPS_DEMO_SITE_PORT:-8410}:8410` | `GET /health`; `GET /__received` counts every non-GET request and GET-form submission it ever received (the D4 proof) and the GETs it served (`get`: pages and files, not `/health` or the counters — the proof's positive control, §14.1.5.2); `mem_limit: 256m`; in-network name `demo-site:8410`; runs as the image's `node` user with a read-only root, `cap_drop: [ALL]` and `no-new-privileges` (M5 Task 24) |

```bash
docker compose --profile scanner up -d --build       # + app, worker, dps-db, migrate (default services)
docker compose --profile scanner ps                  # poll until app, browser-worker, demo-site are healthy
curl -fsS http://127.0.0.1:${DPS_APP_PORT:-8400}/readyz        # components.browser: {"ok": true, "detail": "1 online"}
curl -fsS -X POST http://127.0.0.1:${DPS_APP_PORT:-8400}/api/v1/scans \
  -H 'X-API-KEY: dps_local_dev_key_change_me' -H 'Content-Type: application/json' \
  -d '{"url":"http://demo-site:8410/apply","scope":{"max_pages":25,"deny":["/logout","/admin"]},
       "auth":{"storage_state_ref":"dps/ui/demo-onboarding"},"fill":{"personas":5,"locale":"en_IN"}}'
                                                     # 202 + key material and seed (shown once)
curl -fsS http://127.0.0.1:${DPS_DEMO_SITE_PORT:-8410}/__received   # "non_get":0 on a fresh demo-site
docker compose --profile scanner down
```

**Demo session.** `docker/secrets/default/dps/ui/demo-onboarding/storage_state.json` is the demo
site's session cookie (`demo_session`, domain `demo-site`) — a **local demo literal**, committed like
the compose demo key; it opens nothing but the demo site. The `demo-secrets` one-shot (profiles
`sources`, `scanner`, `full`) copies it read-only into the stack's `dps-secrets` volume under the demo
tenant's folder, beside M2's and M3's demo refs; the browser-worker reads that volume like the worker.
Storage states resolve tenant-scoped (`DPS_SECRET_DIR/<tenant>/<ref>/storage_state.json`, SEC-2 layout,
through `dps.secrets.resolve_secret_ref`), so ref `dps/ui/demo-onboarding` works for tenant `default`
(the bootstrap key without `X-DPS-Tenant`); another tenant needs its own folder (`POST /scans` refuses a
reference into another tenant's folder, SEC-5). Staging/prod mount real storage states the same way,
from a secret, never from the repo.

**Verified 2026-10-13 on this lane** (`COMPOSE_PROJECT_NAME` `dps-wt-m4`, `DPS_IMAGE_TAG=m4`,
app 8414, demo-site 8420): `docker compose --profile scanner up -d --build` → app, worker,
browser-worker, demo-site, dps-db healthy, migrate exited 0; `docker compose exec browser-worker
python -c "…m.version('playwright')"` → `1.55.0`, running as uid 10001; app `/readyz` `browser: ok, 1
online`; worker `:9401/readyz` `browser: chromium 140.0.7339.16`; the scan above → `succeeded` in
68 s (21 pages done, 4 skipped, 63 fields, 9 risk flags, 73 POST + 1 GET blocked by the guard,
`non_get_allowed` 0, evidence and CSV/JSON exports written; the logged-in `/profile` page was
scanned, so the mounted storage state was read); the demo site's `/__received` stayed at `non_get
0, form_get 0, logout_hits 0, admin_hits 0`; a screenshot the browser-worker stored was served by
the app (`GET /api/v1/artefacts/{id}` → 200 `image/png`); the compose logs (168 lines) carry none of
the demo values, the session cookie, the scan's key or seed.

**Traps (M4).**

1. Host browser tests need Chromium 1187: `python -m playwright install chromium` once (the host
   cache may hold other builds — 1148, 1234, 1243 — that belong to other Playwright versions).
2. Chromium in Docker needs shared memory: compose sets `shm_size: 1gb` and the worker also passes
   `--disable-dev-shm-usage` (then Chromium uses `/tmp`, the 512 MiB tmpfs it inherits from the
   worker).
3. The browser image must run as uid 10001 (see above); with the base's `pwuser` every artefact save
   fails with a permission error on the shared volume.
4. Only registered hosts are scanned (`DPS_SCAN_REGISTERED_HOSTS` locally, `dps_tenant.registered_hosts`
   in production); `http://demo-site:8410` is the in-network address — from the host the demo site is
   `127.0.0.1:${DPS_DEMO_SITE_PORT:-8410}`, which the browser-worker container cannot reach.
5. HAR capture is not supported (`capture.har: true` → 422 `har_not_supported`).
6. The demo site's counters only grow; compare before/after (the tests do) or restart `demo-site`
   for a fresh `non_get: 0`. With the compose demo-site on the lane's port, host tests use it
   instead of starting their own `node`.
7. Never `up --wait` (the `migrate` one-shot exits); poll `docker compose ps`.
8. Right after `up` (or an app restart) the app's `/readyz` reads `browser: unknown` until its first
   heartbeat refresh (≤ 15 s). The smoke test waits up to 60 s for it; a script that reads it once
   must do the same.
9. On a lane, the demo site's host port is the lane's `DPS_DEMO_SITE_PORT` (8420 on `dps-wt-m4`);
   reading `127.0.0.1:8410` there reads another stack's counters. `./lane.sh` exports
   `DPS_TEST_DEMO_SITE` for the tests and the smoke test.

**Smoke and the compose-level scanner gate (Task 24).**

```bash
docker compose --profile scanner up -d --build                       # poll `docker compose ps` (trap 7)
DPS_SMOKE_M2=off DPS_SMOKE_SCANNER=1 python tools/smoke_test.py      # 23 Layer 1 + 11 scanner checks
```

The eleven scanner checks: browser worker online; scan accepted with key and seed (dropped, never
printed); scan succeeded; ≥ 20 personal fields with `autocomplete_enabled_sensitive`,
`pii_in_query_string` and `pii_rendered_unmasked` flagged; the demo site's `non_get`, `form_get`,
`logout_hits` and `admin_hits` unchanged and `non_get_allowed` 0 (since M5 Task 24 with two positive
controls, §14.1.5.2: the site's `get` count moved by at least the scan's `pages_visited` — it served the
scan — and the guard blocked at least one non-GET request); the identity page's four parallel-view
screenshots; one served as a PNG; a masked capture served; the persona export (5
personas) holding none of the demo site's customer values; the evidence pack's `scan.submissions`
and `scan.non_get_allowed` 0; `/scanner/new` served. With `--profile sources` up too, leave
`DPS_SMOKE_M2` unset to add the nine job checks. Verified 2026-10-14 on this lane (app 8414,
demo-site 8420, the app restarted just before the run): **all 34 checks passed** (2 OCR checks
skipped without `--profile docs`), scan `succeeded` with 21 pages, 63 fields (51 personal), 9 risk
flags, 3 masked captures, 71 POST + 1 GET blocked, every counter +0; without `DPS_SMOKE_SCANNER` 23
checks, as before. A scan of the compose logs (app, worker, browser-worker) and of the smoke output
for the demo site's ten customer values other than the birth date, the name's parts, their digits-only forms and the
demo session cookie found 0 hits.


### 14.3 M4 pre-merge — main (M5 + M3) merged into lane `m4` (2026-10-14, WP-I)

`git merge main` into `m4` (`b34dd07`, main `6fa2da5`; `4e55da3` adds main's two later docs commits):
23 files conflicted; both sides kept. Migrations: M2 001–010, M3 020, M4 030–031, M5 040–045 (each
number once). Compose: one file with profiles `docs`, `sources`, `oracle`, `scanner`, `tokenizer`, `full`
(browser-worker, demo-site, tokenizer-mock, Oracle, MongoDB beside M2's services); every host port a
defaulted variable. Console: the scanner pages (`/scanner`, `/scanner/new`, `/scanner/scans/:id`) beside
M5's Admin, Policies, Reports and Evidence pages; `npm run dev:fixtures` installs M5's fixture server and
the scanner fixtures on top of it. App: the scans router sits beside admin/reports/policy-store, the
lifespan runs the browser readiness probe and M5's key refresher.

**Adaptations** (each test-first; commits `fix(integrate): …` / `test(integrate): …`, details in
`docs/superpowers/build/M4-DEVIATIONS.md` "M4 pre-merge"):

- Migration 031 adds `registered_hosts` `IF NOT EXISTS`: main's databases ran M5's 040 (which adds it
  too) before 030/031 ship, and the runner applies pending files out of number order.
- **SEC-5:** a scan's `storage_state_ref` is resolved by main's `resolve_secret_ref(tenant_id, ref,
  settings)` and checked at `POST /scans` with a connection `secret_ref`'s rule: another tenant's
  folder 403 `secret_ref_foreign_tenant`, an unlisted `_shared/` one 403 `secret_ref_not_shared`, a
  hidden or reserved segment 422 `secret_ref_invalid`; `_shared/<ref>` sessions work.
- **SEC-2:** the browser-worker reads the stack's `dps-secrets` volume (the tree the app checks refs
  against); `demo-secrets` (now also in profile `scanner`) copies the committed demo session into it
  under `default/`.
- **M5 keys:** database keys with scope `scan` drive every scans route (test only; no change needed).
- **M5 posture:** the browser-worker checks `assert_production_posture(settings, role="browser-worker")`
  — every M2/M4/M5 rule except the API keys, CORS, OCR and tokenizer-token rules of components it does
  not run (§6).
- **M5 policy store:** `policy: "@name[:vN]"` on `POST /scans` resolves at accept; the stored spec pins
  the body the browser-worker runs; the scan evidence's `policy.version` names it; a relaxed body is
  `verify_warn_disallowed` (accept and run).
- **Main's discovery rules in scanner classification:** the shared label dictionary (role names,
  address lines, document numbers); a contact value next to a channel field is `MIXED_PII`; a field
  whose shown value carries MEDIUM+ findings is classified by them (`FREE_TEXT` for findings inside
  text) — never "not personal".
- **Overview:** the "Layer 3 · UI scanner" card counts personal fields (`layers.ui.personal_fields`),
  not every field inventoried.

**Traps.** (1) A lane that runs the scanner and the tokenizer needs its own `DPS_TOKENIZER_MOCK_PORT`:
both default to host port 8420 on `dps-wt-m4` (`.env` sets 8424). (2) `--profile scanner` now runs
`demo-secrets` too; its storage state lands in the volume, so an edit to the committed demo session
needs `docker compose up -d demo-secrets` (or `down -v`). (3) A top-level folder in `DPS_SECRET_DIR` that
is not the tenant's (e.g. an old `dps/` from before SEC-2) makes every ref starting with that segment
`secret_ref_foreign_tenant`/`secret_unavailable` — remove it. (4) `tests/console/test_console_m2.py`
needs the connections the smoke test creates (`bank-restore-rw` too): run the smoke test first.

**Verified 2026-10-14 on this lane** (`dps-wt-m4`, `DPS_IMAGE_TAG=m4`, app 8414, demo-site 8420,
tokenizer-mock 8424; `docker compose --profile full up -d --build`): ruff format/check and mypy clean;
`pytest -q` with every service up 3413 passed (2 merged tests adapted afterwards); with
`DPS_TEST_REQUIRE_SERVICES=1`: `docs or ocr` 139 passed (2 need a host tesseract), `oracle or mongo` 162
passed (1 by design), `browser` 166 passed (27 OCR capture tests need tesseract: 27/27 passed with
`DPS_TEST_TESSERACT_CMD` running the app image's tesseract), the whole non-browser suite 3254 passed;
frontend 42 node + 356 Vitest tests and the build; smoke **52/52** (`DPS_SMOKE_SCANNER=1
DPS_SMOKE_REQUIRE_OCR=1 … --m3`: Layer 1, documents and OCR, the M2 job checks, the three-engine group
3/3 consistent, the scanner's 11). D4: two more scans of the demo site (synthesize and obfuscate, 21
pages and 63 fields each, 60 POST + 1 GET blocked) left `/__received` at +0 with `non_get_allowed` 0 and
`scan.submissions` 0; OCR of every stored screen found no demo value on the filled screens
(`application_ref` stays readable on `/apply/review`, M4's open N9); the logs of all 15 containers hold
none of the demo values, the session cookie, the scans' keys or seeds; a console walk of every M1–M5
page (light/dark, 1440/390 px) had 0 console errors, 0 5xx and no horizontal scroll at 390 px.
Known flake (main's code), **fixed in S2 fix round 1 (§14.1.3.1)**: `tests/connectors/test_synthesize_copula.py::
test_surrogate_job_on_the_seeded_demo_customers` failed once in ~8 runs with one synthetic
`national_id` equal to a demo-source one — the demo seed (`tools/seed_model.py`) drew its SSNs and
Aadhaars from the same never-issued ranges the D-A surrogates use; the run's own identifier check
compares with the source, so such a run is review-required, never silently written. Reports:
`~/.cache/dps-build/reports/S2-premerge.md`.

#### 14.3.1 S2 fix round 1 (2026-10-14) — what changes for a build, a deploy or a demo

Details and tests: `docs/superpowers/build/M4-DEVIATIONS.md` and `M4-DEVIATIONS-console.md` "S2 fix
round 1"; report `~/.cache/dps-build/reports/S2-fix-r1.md`.

- **Demo data model 2** (`tools/seed_model.py` `DEMO_MODEL_VERSION`): demo Aadhaar-shaped ids start with
  `0` and demo SSNs use area `666`, ranges no D-A surrogate draws, so a synthesize run of the demo can
  never write a demo value (the seeded-demo D-A tests are deterministic now). Every seed marker carries
  the model version; **a stack seeded by model 1 is reloaded automatically** (same `--rows`/`--seed`, no
  `--reset`): the compose `seed-sources` one-shot reloads PostgreSQL `bank` (and `bank_restore`) on the
  next `docker compose up`, and `python tools/seed_sources.py --target all --rows 20000 --seed 7`
  reloads Oracle and MongoDB. Anything that cached demo ids (screenshots, notes) shows model-1 values.
- **Captures:** text in a read same-origin iframe is masked in the frame's viewport, mapped through its
  content box and cut to what the frame shows; a transformed or zoomed read frame is painted over whole
  (`FRAME`) — fail closed.
- **API:** `/jobs/{id}` routes refuse a scan to a key without `scan` (403 `scope_required`); `GET /jobs`
  lists no scans for such a key. `GET /admin/posture` gains `scan_fallback_hosts` (the
  `DPS_SCAN_REGISTERED_HOSTS` list on local/dev, `[]` elsewhere); recent-job briefs and evidence
  summaries of scans name the host (plus `forms`, `personas`).
- **Console:** below 1100 px a menu button opens the navigation drawer (mockups' `vault.js`); scan
  rows read "UI scan of <host> · … pages · … forms · … personas · … submissions"; Admin › UI-scanner
  hosts states the local/dev fallback instead of "scans are refused".

#### 14.3.2 S2 fix round 2 (2026-10-15) — what changes for a build, a deploy or a demo

Details and tests: `docs/superpowers/build/M4-DEVIATIONS.md` and `M4-DEVIATIONS-console.md` "S2 fix
round 2"; report `~/.cache/dps-build/reports/S2-fix-r2.md`.

- **Captures:** every mask is placed where the full-page screenshot draws the value, using the
  browser's own position of the page's viewport (CDP `Page.getLayoutMetrics`, `dom.viewport_box`)
  instead of `scrollX`/`scrollY`: pages whose root snaps (`scroll-snap-type`) and RTL or
  `vertical-rl` pages wider than the viewport are masked correctly; scroll snapping is switched off
  while a capture is taken, and a page that scrolls between the measurements and the screenshot is
  withheld (fail closed). A closed select is classified and masked by what it draws (its selected
  option's `label` attribute when it has one), and a personal select stays masked in the filled
  screens (the fill can only pick one of the page's own options). No setting change; the
  browser-worker image must be rebuilt to carry it.
- **Reports / Overview:** a UI sweep is a scan's own verification — succeeded = passed, an evidence
  pack with a verification error (`submission_detected`) = failed and listed under Failed sweeps
  (`where: "scan"`, linked to the scan); a scan that failed before it crawled (`secret_unavailable`,
  …) or was canceled is no sweep. The Overview posture counts UI sweeps too. No migration: UI sweeps
  are read from the job and evidence tables, so past days need no roll-up refresh.

### 14.4 M4 integrated on `main` (2026-10-15, WP-I)

`git merge --no-ff m4` (`5959a75`, `merge: M4 into main`): `m4` already held main up to `144cc65` (the M4
pre-merge, §14.1.3); main had gained the S1-M3 post-merge fixes `5c11e49` and `d2fdc3f`. One conflict (contract
§8.1, the `admin` scope: main's "every write to a production-tagged connection" and `m4`'s "routes that
serve any job kind check its kind" — both kept) and one semantic conflict (both sides imported
`EvidenceOut` into `EvidencePack.test.tsx`; Vitest passed, `tsc --noEmit` — the console build in the app
image — failed; one import kept). Migrations on main: 001–010, 020, 030–031, 040–045 (030/031 applied on
the next `docker compose up`).

**Fixed on main during the merge** (test-first; details in `M4-DEVIATIONS.md` and `M4-DEVIATIONS-console.md`
"M4 merged into main"):

- **A closed select is masked by what it draws** (S2 captures-privacy r3 B1): whatever its selected option's
  `value` (an empty or blank one included), wherever that option sits in a long list (the selected options
  are read apart from the first 300), and a customizable select (`appearance: base-select`) by its button
  text — boxed whole (`SELECT`) whenever it draws text. A select classified personal is masked even while
  it draws its placeholder ("Choose…"); a placeholder is no value of the page (it is not swept for
  elsewhere and takes no obfuscated capture of its own).
  The browser-worker image must be rebuilt to carry it.
- **A stopped run is worded by its batch** (S2 console r3 B1): Reports › Failed sweeps no longer says
  "nothing reached the target" (a table run commits per batch); a failed pack that wrote rows reads
  "stopped · N rows written before it" and "failed closed"; the evidence pack's post-write row says the
  written rows were not re-read.

**Run main with the scanner:** `DPS_TOKENIZER_MOCK_PORT=8430 docker compose --profile docs --profile sources
--profile oracle --profile scanner --profile tokenizer up --build -d` (the `m4` lane's demo site holds host
port 8420). The demo site is on 8410, the browser-worker publishes no host port. The `seed-sources` one-shot
reloads PostgreSQL `bank` to demo data model 2 on the first `up` after the merge (§14.1.3.1); reload Oracle and
MongoDB with `python tools/seed_sources.py --target all --rows 20000 --seed 7`. Capture OCR tests need
`DPS_TEST_TESSERACT_CMD` (a wrapper running the app image's `tesseract` with `--network none`; the Mac has
none). The console tests overwrite the tracked `docs/superpowers/build/m3-shots/` PNGs (A-N2).

**Verified 2026-10-15 on main** (`40d8b34`; the five profiles above, images rebuilt from the tree — every
file under `dps/` identical in `app`, `worker` and `browser-worker`, the served console bundle equal to the
host build): ruff format/check and mypy clean in `.venv` and in a fresh venv with every extra
(`[ner,fpe,vertex,docs,db,connectors,uitest,e2e,scanner,tabular,synth-ml,dev]`; its extras tests 92 passed,
2 need the spaCy model). With `DPS_TEST_REQUIRE_SERVICES=1` (and `DPS_TEST_TESSERACT_CMD` running the app
image's tesseract): `pytest -q` **3598 passed**, 8 skipped (Mongo composite keys, 2 host tesseract, MOSTLY
and pandas ×3, spaCy ×2); `docs or ocr` 139 passed (2 host tesseract); `db or pg_source or s3` **434**;
`oracle or mongo` **178** (1 by design; a foreign test run aimed at main's default ports broke one seeding
test in the first pass — clean on the rerun); `browser` **271**; frontend 44 node + 377 Vitest tests and
the build; smoke **52/52** twice (`DPS_SMOKE_SCANNER=1 DPS_SMOKE_REQUIRE_OCR=1 … --m3`: 21 pages, 63
fields, 51 personal, 60 POST + 1 GET blocked, `non_get_allowed` 0, 3 masked captures, M3 consistency
3/3). D4: the demo site's `/__received` counters moved only with the deliberate positive controls of the
whole suite and the browser gate (+8/+2/+1/+1 each) and the foreign run, never during a smoke;
`dps_llm_raw_pii_sent_total` 0; the logs of all 15 containers hold 0 tracebacks and 0 demo values. The
rebuilt browser-worker image, run with `--network none` over the S2 r3 verifier's select pages, reads
none of their made-up values in any capture (94 PNGs OCR'd, every control readable). Report:
`~/.cache/dps-build/reports/S1-merge-m4.md`.

### 14.5 `m4` merged into finish stream `fsec` (2026-10-15)

`git merge --no-ff m4` on branch `fsec` (merge commit `c1d8c36`): the UI scanner comes to the
security-review stream for its own review and Task 8B. Three conflicts, both sides kept (contract,
`dps/layers/connectors/discovery.py`, `dps/posture.py`). What changes for a build, a deploy or a demo
(details: security review §3.24, `docs/superpowers/build/M5-DEVIATIONS.md` "M4 merged into `fsec`"):

- **Images:** both images (`data-privacy-services`, `data-privacy-services-browser`) carry the merged
  code; the browser image installs the core dependency `regex` (policy patterns) through
  `pyproject.toml` like the app image — rebuild both.
- **Browser-worker:** sizes its pattern matcher processes from `DPS_PATTERN_PROCESSES` (default 2;
  single-threaded children started on first use) like the app and the worker. A scan whose policy
  pattern runs past `DPS_PATTERN_CPU_SECONDS` ends `failed` with `custom_pattern_too_slow` at once (no
  retry; key purged; a failed evidence pack names the code).
- **Posture:** `DPS_TOKENIZER_PROFILES` is required (with the tokenizer enabled, staging/prod) for
  the app and the worker only; the browser-worker never tokenizes and boots without it.
- **Data at rest:** a scan's policy body (inline, or an `@name` reference resolved at accept) is
  sealed with the job KEK in `dps_job.spec_enc`; `dps_job.spec.policy` holds its value-free label
  `{sealed, name, version, sha256}` (`dps.rekey` re-seals `spec_enc` of every job kind). Scans accepted
  before this change keep their body in `spec` and still run.
- **API:** `POST /scans` honours `Idempotency-Key` across a KEK rotation and removes a scan whose
  creation audit a strict sink refused (503 `audit_unavailable`); uncoded scan-spec refusals are the
  app's scrubbed 422 (`{"detail": [{loc, msg, type}], "code": "invalid_request"}`); the scan CSVs guard
  `+`/`-` cells like the L2 export; `dps_scan_fields_total` labels custom entity types `custom`.
- **Lane `fsec` stack:** `docker compose --profile full up -d` from `dps-wt-fsec` (ports in its `.env`:
  app 8416, dps-db 5483, source-pg 5484, MinIO 9070/9071, Oracle 1526, Mongo 27022, OCR 5066, demo
  site 8426, tokenizer mock 8436); gates of the merged tree: report
  `~/.cache/dps-build/reports/FSEC-merge-m4.md`.

#### 14.5.1 S5 wiring and Task 8B on `fsec` (2026-10-15) — what changes for a build, a deploy or a demo

Details: security review §3.25–§3.27, `docs/superpowers/build/M5-DEVIATIONS.md` (Task 8B, Task 17 S5).

- **Images:** both images carry the change — rebuild both.
- **`POST /scans` resolves the start host** (the SSRF guard, S5): the **app** pod must resolve the
  tenant's registered UAT hosts (a name that does not resolve from the app answers 422
  `host_forbidden`), and a host that is — or resolves to — a link-local/cloud-metadata, multicast,
  unspecified or reserved address, a numeric host encoding, or loopback outside `DPS_ENV=local`, is
  refused (RFC 1918 stays allowed). The browser-worker checks the start URL again before a scan runs.
- **Browser-worker:** Chromium is launched with `--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE
  127.0.0.1` — it resolves no name itself; the scan's scope proxy (in the browser-worker process)
  resolves every hop's host through the pod's DNS and dials only addresses that pass the same rule
  (a refused hop is counted `blocked` / `host_forbidden` in the scan's request log). In compose,
  `demo-site` resolves to the network's private address (allowed); `localhost`/`127.0.0.1` targets
  work only with `DPS_ENV=local`.
- **T3 labelling (opt-in per policy, `detect.tiers` lists `llm_label`):** discover jobs (the
  **worker**) and scans (the **browser-worker**) now call the LLM gateway when such a policy is used
  and a real provider is configured — the browser-worker then needs the same LLM settings and
  secrets as the app (`DPS_LLM_PROVIDER`, Stellar/COIN or Vertex) and egress to the LLM endpoint
  (OpenShift NetworkPolicy). Without them, or with `stub`, labelling is skipped (behaviour
  unchanged). Prompts carry metadata and value shapes only and pass the leak guard and the egress
  verifier (`DPS_LLM_EGRESS_CHECK=strict` in staging/prod); `progress.metrics.llm` of the job counts
  the calls. A scanned field's label (page text) goes into the prompt only when the browser-worker
  runs NER (built with `WITH_NER=1`, `DPS_NER_ENABLED` not `false`, `ner` in the policy's tiers),
  redacted of every span any detector tier reports — at any score, whatever the policy's
  `detect.min_score` — with its digit/`@` tokens as shapes; without NER, fields are labelled by their
  name, type, placeholder shape and hints (security review §3.26).

### 14.6 M5 Task 24 — images, the `full` stack, the smoke M5 block, the flow report (finish stage, lane `fimg`, 2026-10-14)

Built on `main` as merged so far (M0–M3 + M5; M4 not yet merged) in `dps-wt-fimg` (branch `fimg`;
`.env`: `DPS_IMAGE_TAG=fimg`, app 8417, dps-db 5493, source-pg 5494, MinIO 9080/9081, Oracle 1527,
Mongo 27023, OCR 5076, demo site 8427, tokenizer mock 8437). Deviations from the plan:
`docs/superpowers/build/M5-DEVIATIONS.md` "Task 24"; measurements and proofs:
`~/.cache/dps-build/reports/S4-impl.md`.

**What changed.** `Dockerfile`: `ARG EXTRAS` (opt-in extras, validated), the arbitrary-UID rules of
§2 (user 10001 in group 0, `/app` group-readable and never group-writable, `/app/var`
group-writable and setgid, `HOME=/tmp`), the console stage on `$BUILDPLATFORM`;
`docker/entrypoint.sh`: `umask 0002`; compose: `DPS_SYNTH_TABULAR_ENGINE`, `DPS_KEY_CACHE_SECONDS=5`,
`DPS_REPORT_PDF_ENABLED` for every role, the app waits for a running tokenizer mock; `.gitignore`
`/reports/`, `.dockerignore` `reports`; `tools/smoke_test.py` M5 block; new `tools/demo_client.py`
and `tools/flow_report.py`.

**Verified (2026-10-14, lane stack `docker compose --profile full up --build -d`):**

- Images: `docker compose build app` (arm64) and `docker build --platform linux/amd64` both build;
  `DPS_TEST_IMAGE=data-privacy-services:fimg pytest -q tests/test_image_runtime.py` 3 passed on
  arm64 and on amd64 (`DPS_TEST_IMAGE_PLATFORM=linux/amd64`, emulated). The image of `main` before
  this change fails the same test (an arbitrary UID gets `HOME=/` and cannot write the volumes).
- Every role of the app image as uid 1000770000:0 (a compose override with `user:` on app, worker,
  migrate, tokenizer-mock, seed-sources; all read-only) on volumes uid 10001 wrote first:
  `/readyz` ready (api-core, llm-gateway, fpe, tokenizer, ocr, db, jobs, keys); `migrate` and
  `seed-sources` exited 0; `DPS_SMOKE_REQUIRE_OCR=1 DPS_SMOKE_REQUIRE_M5=1 python tools/smoke_test.py
  --m3` 48/49 — every check but `api responses are not cacheable` (S11, M5 Task 17, lane `fsec`);
  `python tools/flow_report.py` exit 0 (92 calls: 82 as expected, 10 scanner calls skipped — no
  browser worker on `main` yet); no `PermissionError`, `Read-only file system` or `StorageError` in
  the logs of app, worker, migrate, tokenizer-mock, seed-sources, demo-secrets or the OCR mock
  (dps-db logs only the integration suite's deliberate grant refusals). As uid 10001 the same:
  smoke 41/42 (without `--m3`), flow report exit 0.
- `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"` 341 passed against that stack.
- `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "docs or ocr or oracle or mongo"` 301 passed, 3 skipped
  (two need a host `tesseract`, one is MongoDB's by-design "no composite keys" conformance case).
  Its M3 console tests rewrite the tracked screenshots under
  `docs/superpowers/build/m3-shots/` (A-N2); they were restored from git, not committed.
- M4 slot-in, checked on a probe image built from branch `m4` with the §8 recipe for
  `Dockerfile.browser`: Chromium launches as uid 10001 and as uid 1000770000:0 with a read-only
  root, `cap_drop ALL` and `no-new-privileges`; the artefact volume is writable, `/ms-playwright`
  is not (read-only root); `m4`'s own image cannot write the volume as an arbitrary UID.
- Value-free proofs: a scan of every container log of the stack and of the flow report for 2 000
  sampled source values per personal column (names, e-mails, mobiles, national ids, tax ids), the
  smoke/flow sample record, the SSN probe, the bootstrap key, the tokenizer token and key, the demo
  KEK, raw DB API keys (`dps_` + 32) and `tk_` tokens — 0 hits; source-vs-target counts on the
  smoke's and the flow's `bank_uat` tables: 0 target values equal to a source value in `full_name`,
  `national_id`, `tax_id`, `email`, `mobile` and `notes` (dates and the FF1 customer ids only
  collide with other customers' values at the domain's occupancy rate, e.g. 62.0 % of shifted
  birth dates fall on a day some customer has against 63.7 % of the span's days occupied).

**Review fix r1 (2026-10-14, review S4 r1 B1/N2/N3).** The store wrote every artefact and local
export object `0600` (`tempfile.mkstemp`), so after a UID change on the same volumes every earlier
artefact answered 500 and every earlier Evidence page 502 (`storage_error`). Now
`dps/storage.py` creates each object with mode `0660` less the umask (`OBJECT_MODE`; owner and
group, never other users; an S3 upload's spool in the temp directory stays `0600`), and
`LocalObjectStore.stream`/`exists` map every `OSError` to `storage_error` like `get`. Verified:

- `tests/test_image_runtime.py` now goes through `LocalObjectStore` on an artefact and an export
  volume (uid 10001 stores, uid 1000770000:0 reads, streams, replaces, writes and deletes next to
  it, uid 10001 reads back). On the image of `74625d6` it fails (objects `0o600`; the new UID's
  `get` raises `storage_error: PermissionError`); rebuilt, 3 passed on arm64 and on amd64.
- Live, lane stack `--profile full`: run A as uid 10001 (`smoke_test.py --m3` 48/49, the only
  failure S11; `flow_report.py` exit 0) left 25 objects, all `660 10001:0` in `2775` directories.
  With every role of the image as 1000770000:0 on the same volumes, all 25 answered 200 on
  `GET /artefacts/{id}` and all 8 packs 200 on `GET /evidence/{id}/html` and `/pdf` (before the
  fix: 500 and 502). Run B as the new UID: smoke `--m3` 48/49 (S11), flow exit 0; back as uid
  10001, all 43 artefacts and 16 packs answered 200. A `pvc` CSV export as uid 10001 and another as
  the new UID into the same `base_path` both succeeded with a verified pack. At the end, as the new
  UID, all 56 artefacts and 18 packs answered 200; the volumes held only `2775` directories (28)
  and `660` files (62), group 0, of both UIDs.
- `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"` 341 passed; `-m "docs or ocr
  or oracle or mongo"` 301 passed, 3 skipped (the M3 screenshots they rewrite were restored from
  git). A scan of the stack's current container logs and of the saved smoke, flow and export
  outputs (1.08 MB) found 0 of the usual needles and no `PermissionError`, `StorageError` or
  traceback.

#### 14.6.1 M4 slot-in — `m4` merged into `fimg` (2026-10-15)

`git merge --no-ff m4` (`83bc5f3`; `m4` at `cbbac38`: M4 with `main` up to `144cc65` merged into it and
S2 fix rounds 1–2, the commit `main` merged as `5959a75`). One conflict, this file: both sides kept —
M4's §14.1.2 and §14.1.3 keep their numbers, this task's record moved from §14.1.2 to §14.1.4 (now §14.1.6: main's
own §14.1.4 records the M4 integration, §14.1.5 the `m4` merge into `fsec`). Adaptations (`d8c629c`, `58fd122`, `78f0017`; details in
`docs/superpowers/build/M5-DEVIATIONS.md` "Task 24", report
`~/.cache/dps-build/reports/FIMG-merge-m4.md`):

- `Dockerfile.browser` follows §2's arbitrary-UID rules (the recipe §8 carried) and drops the write
  bits uv leaves on `/opt/venv/.lock` (`0666`); `tests/test_browser_image_runtime.py` checks a built
  image (red on `m4`'s image: umask `0022`, `storage_error: PermissionError` for a second UID).
- `tools/smoke_test.py`: `DPS_SMOKE_SCANNER` `auto` (default; the eleven scanner checks run when the
  demo site answers, i.e. under `scanner` and `full`), `on`/`1` (a missing browser worker or demo
  site fails), `off`/`0`.
- `tools/flow_report.py`: `--require-scanner` (the `full` gate); the scanner block waits up to 60 s
  for a freshly started app's `browser` readiness instead of skipping; every flow row is a route the
  app serves (the console's `/` and SPA fallback, served when `frontend/dist` is built, are not §9
  routes).
- `.dockerignore` keeps `tools/dev` out of the image; §8's re-permission command is the measured one.

**Verified 2026-10-15 on this lane** (`docker compose --profile full up --build -d`: 15 services,
browser-worker and demo-site included; `/readyz` ready with every component ok but `ner`
(patterns-only by design), `browser: 1 online`):

- Gates: ruff format/check, `mypy dps` (150 files) clean; with every service up
  `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_TESSERACT_CMD=<the app image's tesseract> pytest -q` 3581
  passed, 14 skipped, 1 failed — the flow report's route check, which counted the console routes of
  a built `frontend/dist`; fixed in `58fd122`. By marker: `docs or ocr` 139 passed (2 need a host
  tesseract), `db or pg_source or s3` 409, `oracle or mongo` 162 (1 by design), `browser` 238. The
  other skips: the opt-in image runtime tests (run separately below), `ml` (MOSTLY not installed),
  NER (no spaCy model). Frontend: `npm ci`, 44 node + 366 Vitest tests, `npm run typecheck`,
  `npm run build` (bundle check clean). The M3 console tests rewrote the 54 tracked shots under
  `docs/superpowers/build/m3-shots/` (A-N2); restored from git, not committed.
- Images: `docker compose --profile full build` (arm64; browser image 4.24 GB, of which the
  permissions layer is 86 kB) and `docker build --platform linux/amd64 -f Dockerfile.browser` (665 s
  cold under emulation). `DPS_TEST_BROWSER_IMAGE=… DPS_TEST_IMAGE=… pytest -q
  tests/test_browser_image_runtime.py tests/test_image_runtime.py` 6 passed on arm64; the browser
  runtime test 2 passed on amd64 (the cross-image case needs an amd64 app image of this commit).
- Stack as uid 10001: `DPS_SMOKE_SCANNER=on DPS_SMOKE_REQUIRE_OCR=1 DPS_SMOKE_REQUIRE_M5=1
  python tools/smoke_test.py --m3` 59/60 (Layer 1, M1, the scanner's 11, M2's 9, M3's 7, M5 7/8:
  the only failure is `api responses are not cacheable`, S11 on `fsec`); the same without
  `DPS_SMOKE_SCANNER` (auto) 59/60 with the scanner block run; `python tools/flow_report.py
  --require-scanner` PASS, 92 calls, 0 skipped (the ten scanner calls ran; cancel 409
  `not_cancelable`). With the demo site stopped, auto prints a note and skips the block; `on` fails
  with `demo site answers`.
- Every role of both images as uid 1000770000:0 (compose override with `user:` on app, worker,
  migrate, tokenizer-mock, seed-sources and browser-worker; all read-only) on the volumes uid 10001
  wrote: `/readyz` ready, browser 1 online, the browser-worker's processes run with umask `0002`
  and `HOME=/tmp`; all 103 artefacts of the first run (76 screenshots and 3 captures the
  browser-worker stored) answered 200, as did their 10 evidence packs (html and pdf; read slowly —
  a burst meets the API's 50 requests/s limit with 429); smoke `--m3`
  59/60 (S11), flow `--require-scanner` PASS 92/0. Back as uid 10001: all 215 artefacts and 20
  packs of both runs answered 200. The volumes held only `2775` directories and `660` files, group
  0, of both UIDs.
- D4: the demo site's `/__received` counters did not move during any smoke, flow or probe scan
  (`non_get_allowed` 0; 60 POST + 1 GET blocked per smoke scan). The whole test suite moves them by
  +8 non-GET, +2 form GETs, +1 `/logout`, +1 `/admin` — exactly its own positive control
  (`test_positive_control_the_demo_counts_what_it_receives`), as on lane `m4`.
- OCR (the app image's tesseract) of every stored screen of a smoke scan: the filled screens
  (12 synthesize, 12 obfuscate) show no demo value and the scan's own personas (positive control);
  the 3 masked captures show none except `application_ref` on `/apply/review` (M4's known N9, not a
  detected entity), as on lane `m4`.
- Logs and artefacts: every container log of the stack and every saved tool output (74 sources,
  6.97 MB) and the artefact and export volumes (0.8 MB) hold 0 of: the smoke/flow sample record,
  the SSN probe, the bootstrap key, the tokenizer token and key, the demo KEK, the demo site's
  customer values and session cookie, a probe scan's DEK and seed, 2 000 sampled source values per
  personal column of PostgreSQL, Oracle and MongoDB, raw `dps_` keys, `tk_` tokens; no traceback in
  any container; `dps-db` and `source-pg` log only the integration suite's deliberate refusals
  (append-only, RLS, grants, check constraints, bad passwords, a read-only transaction).
  `dps_llm_raw_pii_sent_total` stayed 0.
- Source vs target (counts only, after the last smoke): `bank_uat.customers_smoke` (20 000),
  `customers_flow` and `customers_flow_b` (500 each) — 0 same-row survivors in every personal
  column and 0 target values equal to any source value in names, e-mails, mobiles, national ids,
  tax ids and notes; Oracle `CRM_MASKED.CUSTOMERS_SMOKE` and MongoDB `crm_uat.profiles_smoke`
  (20 000 each) — 0 for every personal column; FF1 customer ids equal another customer's id 6 times
  in 20 000 and shifted birth dates fall on occupied days (domain collisions only).

**Traps.** (1) With `init: true` the browser-worker's PID 1 is `docker-init` (umask `0022` in
`/proc/1/status`); its own processes (the worker, the Playwright driver, Chromium) run with `0002`.
(2) Under `DPS_SMOKE_SCANNER=auto` the smoke decides by the demo site on `DPS_TEST_DEMO_SITE` (or
`127.0.0.1:${DPS_DEMO_SITE_PORT:-8410}`): run it with the lane's variables, or another stack's demo
site decides. (3) `tests/test_flow_report.py` builds the app in-process; a built `frontend/dist`
adds the console routes, which are not flow rows.

#### 14.6.2 S4b — the scanner in the `full` stack: D4 with positive controls, unprivileged mocks, amd64 and arbitrary-UID runs (2026-10-15)

Finish-stage work (orchestration stream S4b: M5 Task 24 for the scanner). Merges: `4eeac2a` (main up
to `40d8b34`: the M3 post-merge fixes and the M4 integration fixes, among them the select-drawn
masking the browser image now carries) and `5c54dda` (main's docs commit `8ea220a`; this lane's
record became §14.1.5 first, §14.1.6 at the release merge, `93d4b4d`). Changes, each test first (`7561e89`, `f39df5b`, `9db127f`;
details in `docs/superpowers/build/M5-DEVIATIONS.md` "Task 24", report
`~/.cache/dps-build/reports/S4b-impl.md`):

- **D4 with two positive controls** (`tools/smoke_test.py`, `demo/onboarding/server.mjs`): the demo
  site's `/__received` also counts the GETs it served (`get`: pages and files, not `/health` or the
  counters). "no submission reached the site" now also needs the site to have served at least one GET
  per page the scan visited (`pages_visited`; the site sends `no-store`, so every visit fetches its
  page) and the request guard to have stopped at least one non-GET request (the pages' autosave posts
  and draft flushes, which filling provokes). Unmoved counters of a site the scan never visited
  (another lane's demo site, a stale port) or of a scan whose pages never tried to send no longer
  pass, and a demo site without `get` fails the check. The verdict prints counts only:
  `non_get +0, form_get +0, logout_hits +0, admin_hits +0; pages served +175 (21 visited); non-GET
  blocked 60; non_get_allowed 0, …`.
- **The local mocks run unprivileged** (review S4 r2 R2-4): `azure-ocr-mock` as uid 10001:0 with a
  read-only root, a 128 MiB `/tmp` tmpfs, `cap_drop: [ALL]` and `no-new-privileges`; `demo-site` as
  the `node` image's own user (uid 1000) with a read-only root, `cap_drop: [ALL]` and
  `no-new-privileges` (§3, §6, §14.1.2; `tests/test_compose_m5.py`).
- **The image runtime tests check the architecture they run:** under `DPS_TEST_IMAGE_PLATFORM` the
  probes must report `platform.machine()` `x86_64` (`linux/amd64`) or `aarch64` (`linux/arm64`).

**Verified 2026-10-15 at `5c54dda`** (`docker compose --profile full`, fresh volumes; 15 services,
browser-worker and demo-site included):

- Gates: ruff format/check (444 files) and `mypy dps` (150 files) clean. With every service up,
  `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_TESSERACT_CMD=<the app image's tesseract> pytest -q`:
  **3688 passed**, 14 skipped (MongoDB's by-design composite-key case, 2 need a host tesseract, the 6
  opt-in image runtime tests run below, 3 MOSTLY/pandas, 2 spaCy), 0 failed, 28 min. Frontend:
  `npm ci`, 44 node + 377 Vitest tests, `npm run typecheck`, `npm run build` (bundle check clean). The
  M3 console tests rewrote the 54 tracked shots (A-N2); restored from git, not committed.
- Images: `docker compose --profile full build` (arm64) and `docker build --platform linux/amd64`
  of both images (`data-privacy-services:fimg-amd64`, `data-privacy-services-browser:fimg-amd64`).
  `tests/test_image_runtime.py` + `tests/test_browser_image_runtime.py`: 6 passed on arm64 and 6 on
  amd64 (`x86_64` under emulation; the cross-image case included: the app image reads, streams and
  replaces what the browser image stored).
- **Run A** — arm64, every role as uid 10001: `/readyz` ready in 29 s, `browser: 1 online`; smoke
  (`DPS_SMOKE_SCANNER=on DPS_SMOKE_REQUIRE_OCR=1 DPS_SMOKE_REQUIRE_M5=1 python tools/smoke_test.py
  --m3`) **59/60**: the only failure is `api responses are not cacheable` (M5 Task 17 S11, lane
  `fsec`); D4 `pages served +175 (21 visited); non-GET blocked 60`, every counter +0; `python
  tools/flow_report.py --require-scanner` PASS, 92 calls, the 10 scanner calls run. The fresh demo
  site had then received 0 non-GET requests in total.
- **Run B** — every role of both images as its **amd64** build (emulated) and as uid
  **1000770000:0** (a compose override with `image`, `platform` and `user` on app, worker, migrate,
  tokenizer-mock, seed-sources and browser-worker; each read-only with `cap_drop: [ALL]` and
  `no-new-privileges`; their processes run `x86_64`, gid 0, umask `0002`) on run A's volumes:
  ready in 33 s, browser 1 online; migrate and seed-sources exited 0; all 117 objects of run A (76
  screenshots and 3 captures the arm64 browser-worker stored as uid 10001) and its 17 packs (html and
  pdf) answered 200; smoke 59/60 (S11), D4 `+175 (21 visited)`, 60 POSTs blocked; flow PASS 92/0. After
  a reseed to 20 000 rows the same again (run B2): smoke 59/60 (S11; M3 runs 20 000 rows on each
  engine, consistency 3/3; 8 min under emulation), 61 POSTs blocked, flow PASS 92/0.
- **Run C** — back to arm64 and uid 10001: smoke with `DPS_SMOKE_SCANNER` unset (`auto`) 59/60, the
  scanner block run; flow PASS 92/0. At the end all 526 objects (370 screenshots and 18 captures
  from every run's browser-worker) and all 48 packs (html and pdf) answered 200; the volumes held
  only `2775` directories (29 of uid 10001, 21 of uid 1000770000) and `660` files (248 / 297), all
  group 0.
- D4: the demo site's non-GET counters moved only during the whole suite (+8 non-GET, +2 GET-form
  submissions, +1 `/logout`, +1 `/admin`: its own positive-control tests), never during the four
  smokes, the four flow reports or a probe scan; `non_get_allowed` 0 everywhere;
  `dps_llm_raw_pii_sent_total` stayed 0.
- OCR (the app image's tesseract) of every stored screen of smoke scans A (arm64) and B (amd64): the
  filled screens show no demo value and the scan's own personas (the positive control); the 3 masked
  captures show none but `application_ref` on `/apply/review` (M4's known N9, not a detected entity).
- Logs and artefacts (counts only): the container logs of runs B2 and C and of the data stores over
  the whole session, every saved tool output (161 sources, 22.4 MB) and the artefact and export
  volumes (1.0 MB) hold 0 of: the smoke/flow sample record, the SSN probe, the bootstrap key, the
  tokenizer token and key, the demo KEK, a probe scan's DEK and seed, the demo site's customer values
  and session cookie, 2 000 sampled values per personal column of PostgreSQL, Oracle and MongoDB, raw
  `dps_` keys, `tk_` tokens, `PermissionError`, `Read-only file system`, `storage_error` or a
  traceback. `dps-db` and `source-pg` log only the suite's deliberate refusals (append-only, RLS,
  grants, check and unique constraints, an immutable policy version, bad passwords, a read-only
  transaction, a statement timeout, an over-long value).
- Source vs target after run B2 (counts only): `bank_uat.customers_smoke` (20 000), `customers_flow`
  and `customers_flow_b` (500 each), Oracle `CRM_MASKED.CUSTOMERS_SMOKE` and MongoDB
  `crm_uat.profiles_smoke` (20 000 each): 0 same-row survivors and 0 target values equal to any
  source value in names, e-mails, mobiles, national ids, tax ids and notes; customer ids (4 in
  20 000) and shifted birth dates meet other customers' values (domain collisions only).

**Traps.** (1) The whole suite leaves Oracle and MongoDB with 200 rows (its seeding tests): reseed
with `python tools/seed_sources.py --target all --rows 20000 --seed 7` before a 20 000-row smoke
(the M3 checks also pass on 200 rows, with 200 sampled). (2) Changing a role's `user` or `platform`
recreates its container and drops its logs: save `docker compose logs` first when they are
evidence. (3) The demo site's `get` counts every GET it serves, from any client: the check needs at
least `pages_visited` of them, so stray GETs cannot make another site's counters pass, but a browser
tab left on the site inflates `pages served`.

### 14.7 OpenShift (M5 Task 25, finish stage, branch `fos`)

Manifests in `deploy/openshift` (Wega layout; full guide and parameter table in
`deploy/openshift/README.md`): `base/` (Deployments `dps-app`, `dps-worker`, `dps-browser-worker`,
each with the init container `wait-for-schema`; Job `dps-migrate-0`; CronJob `dps-migrate-maintain` (audit partitions, daily); Services `dps-app`
and `dps-db` — an ExternalName to the platform PostgreSQL;
Route `dps-app` with edge TLS, HTTP redirected, router timeout 300 s; PDBs `maxUnavailable: 1`;
four ServiceAccounts without tokens; ConfigMap `dps` from `configmap-dps.env`), components
`network-policies` (default-deny + one allow per path), `monitoring` (PodMonitor + PrometheusRule
with the seven alerts of §5 plus `DpsAuditMaintenanceStale`, `DpsRawPiiSentToLlm` critical),
`dev-mocks` (tokenizer mock running a
byte-identical copy of `docker/tokenizer-mock/server.py`, Azure OCR mock), overlays `dev`
(namespace `dps-dev`, one replica each, mocks, stub LLM, TLS egress off) and `prod` (`dps-prod`,
app 3, worker 2, browser-worker 1, no mocks), and `release-example/`.

- **Pod security (`restricted-v2`):** no `runAsUser` (the namespace's arbitrary UID), `runAsNonRoot`,
  `RuntimeDefault` seccomp, read-only root, no privilege escalation, `drop: [ALL]`, no SA token, no
  service links (Services named `dps-*` would inject `DPS_*` variables), `/tmp` an `emptyDir`,
  `HOME=/tmp` in the pod env (so the manifests do not depend on the image's `HOME`), secret files
  mode `0440` read through the SCC-assigned `fsGroup`, images pinned by digest.
- **Secrets are files only** (`DPS_*_FILE`, the M5 posture rule). The app and both workers pass the
  production posture guard at boot, so each mounts `dps-api-keys` and `dps-job-kek`; both workers
  open the API pool and the worker pool at start, so they hold both logins (`dps-pg-app`,
  `dps-pg-worker`; the runtime proof below failed without the first); only the migrate Job and the
  CronJob `dps-migrate-maintain` mount `dps-pg-owner`. The
  browser worker runs with `DPS_LLM_PROVIDER=""`, `DPS_OCR_PROVIDER=off`, `DPS_TOKENIZER_PROVIDER=""`
  and mounts none of their secrets.
- **Start order (S5 review r2, B1):** compose starts app and workers only after its `migrate`
  one-shot completed; Kubernetes has no such order and a release applies the Job and the
  Deployments together. The app verifies the schema once, at boot (`dps/app.py`
  `_JobPlatform._connect`: pending migrations set `db_state=down` and nothing retries), so an app
  pod that started before the release's Job committed kept every `require_db` route at 503
  `db_unavailable` — jobs, connections, groups, snapshots, artefacts, evidence, reports, admin, the
  policy store and `@name` policy references — while staying Ready (`/readyz` needs only
  `api-core`), until restarted; the workers exited on `MigrationDrift` and crash-looped. Each of the
  three Deployments now starts with the init container `wait-for-schema`: its own image,
  `["sh", "-c", "trap 'exit 143' TERM INT; until python -m dps.migrate --verify; do sleep 5 & wait $!; done"]`
  (args, so the app image's entrypoint stays; the trap ends the wait at once on SIGTERM — a shell as
  PID 1 ignores it otherwise and a deleted waiting pod hangs for its grace period), env = the `dps`
  ConfigMap + `HOME=/tmp` + `DPS_PG_PASSWORD_FILE` (+ the browser worker's own `DPS_IMAGE_DIGEST`),
  mounts `tmp`, `ca`, `pg-app` only, the pod's security context, 50m/64Mi requests and
  500m/256Mi limits (one verify: 0.9 s, 34 MiB peak). `tools/openshift_check.py` denies a DPS
  Deployment without such a wait in its DPS container's image (`schema-wait`) and builds the
  wait's `Settings` too. A release that applied a migration cannot be rolled back by image (fix
  forward): older pods would wait for good (`applied but no longer shipped`). Follow-up for main
  (F1/F4): the app should keep re-verifying until verified, or exit, as contract §2.1 says.
- **Metadata DB host:** `DPS_PG_HOST` is the platform host itself (equal to `dps-db`'s
  externalName): verify-full TLS (`DPS_PG_SSL_ROOT_CERT`, `dps/db.py`) checks the certificate against
  the name dialled, which a platform certificate does not issue for `dps-db`.
- **Audit-log partitions (S5 review r1, B1):** `dps_audit_log` has one RANGE partition per UTC month
  and no DEFAULT partition, and only the owner login creates them (`dps.migrate`: this month + 3;
  `--maintain`: the same, plus dropping months older than `DPS_AUDIT_RETENTION_MONTHS`). The migrate
  Job runs once per release, so the CronJob `dps-migrate-maintain` runs `python -m dps.migrate
  --maintain` daily at 03:17 UTC (`concurrencyPolicy: Forbid`, `activeDeadlineSeconds: 1800`,
  `startingDeadlineSeconds: 3600`, 3 Jobs of history) in the migrate Job's pod (a test keeps the two
  pod templates equal but for the argument). Without it every audit write fails about four months
  after a release (`audit_flush_failed` only; no metric). Alert `DpsAuditMaintenanceStale` (warning)
  reads kube-state-metrics: no success for 3 days, or a failed run (`kube_job_status_failed` of a
  `dps-migrate-maintain-*` Job) and never a success (S5 review r2, N1: not `kube_cronjob_created`,
  which OpenShift's kube-state-metrics drops; a CronJob never scheduled at all is not seen).
- **Image digest per container (S5 review r1, B2):** evidence packs carry `DPS_IMAGE_DIGEST` (job
  packs from the worker; scan packs from the browser worker after M4), so the ConfigMap holds the app
  image's digest and the browser worker sets its own in its env (env overrides `envFrom`). The release
  overlay derives both from `images:` with `replacements` (kustomize runs them after `images` in the
  same kustomization; `release-example` shows the block; the browser worker's `wait-for-schema`
  runs the browser image and carries the same entry); `tools/openshift_check.py` denies
  (`image-digest-setting`) a DPS container whose value is unset, not a literal, or not its own image's
  digest (the zero placeholder only warns), and (`db-host`) a `DPS_PG_HOST` that is not `dps-db`'s
  externalName. The ConfigMap also sets `AWS_CA_BUNDLE` to `org-ca.pem` (boto3 reads neither
  `SSL_CERT_FILE` nor `DPS_SSL_CERT_FILE`). `literal-secret` covers every key of every ConfigMap
  (not only `DPS_*`) and the libraries' credentials `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `AWS_SESSION_TOKEN`, `PGPASSWORD` (S5 review r2, N2).
- **Placeholders → release overlay:** zero digests (images and `DPS_IMAGE_DIGEST`), `.invalid` hosts
  (DB, S3, Stellar, COIN, OCR gateway, tokenizer, Route), TEST-NET-1 NetworkPolicy peers (one per
  endpoint: DB `192.0.2.10/32`, gateways `.1`–`.4`, object store `.5`, source databases
  `192.0.2.32/27`, scan targets `192.0.2.64/27`) and the Job name `dps-migrate-0` (renamed per
  release). `python tools/openshift_check.py <dir>` warns on each and denies hardening violations and
  — with `dps` importable — any role whose settings fail `Settings` or (staging/prod) the posture
  guard; `--fail-on-warn` is the release gate. Every JSON6902 `replace` is preceded by a `test` of the
  committed value (tests enforce it).
- **Platform prerequisites:** user-workload monitoring; ConfigMap `dps-ca-bundle` (`pg-root.pem`,
  `org-ca.pem`); RWX PVC `dps-exports` (the worker writes `pvc` targets, the app's connection test
  probes it); the secrets above; network paths for every peer. Connector `secret_ref` folders come
  from `dps-connector-refs` with an `items` layout (or a CSI/Vault mount) at `DPS_SECRET_DIR`.
- **Verified 2026-10-14 (`fos`):** `pytest -m deploy tests/deploy/test_openshift.py` 36 passed, 1
  skipped (`dps.browser_worker` arrives with the M4 merge); `kustomize build` (v5.8.1) of dev, prod
  and the release example; `kubeconform -strict` (Kubernetes 1.29 schemas, the OpenShift 4.14 Route
  schema, the datree CRD catalog for PodMonitor/PrometheusRule): dev 32/32, prod 25/25, release
  example 25/25 valid; `promtool check rules` on the PrometheusRule: 7 rules, SUCCESS. After the S5
  review r1 fix: 52 passed, 1 skipped; `kubeconform -strict` dev 33/33, prod 26/26, release example
  26/26 with the Kubernetes 1.27 and 1.29 schemas and with the OpenShift 4.14 and 4.16 strict
  catalogs (the CronJob included); kustomize 5.8.1 and `kubectl kustomize` 5.7.1 render identically;
  `promtool check rules` 8 rules SUCCESS and `promtool test rules` (stale, recent, never succeeded,
  just created, daily successes) SUCCESS. After the S5 review r2 fix: 60 passed, 1 skipped (full
  unit suite 2198 passed, 488 skipped); `kubeconform -strict` dev 33/33, prod 26/26, release example
  26/26 with the OpenShift 4.14, 4.16 and 4.19 strict catalogs and the Kubernetes 1.27 and 1.29
  schemas, 0 skipped (an unknown field in an init container is refused); kustomize and
  `kubectl kustomize` still identical; `promtool test rules` on the new alert expression: a failed
  run that never succeeded fires after `for: 1h` with OpenShift's series (no `*_created`), a
  CronJob not yet run is silent, a 4-day-old success fires, a 1-day-old one is silent, an old
  failed Job in the history followed by successes is silent, a run still retrying is silent.
- **Runtime proof (no cluster available):** each role's rendered prod pod spec run with Docker —
  the ConfigMap + container env (`DPS_ENV=prod`), every secret a `root:<fsGroup>` 0440 file at its
  mount path, UID 1000680000 with group 0 and the fsGroup, `--read-only`, `--cap-drop ALL`,
  no-new-privileges, `/tmp` (and `/dev/shm`) tmpfs — against a scratch PostgreSQL with TLS whose
  certificate names only `dps-db` (image `data-privacy-services:fimg`, browser image from the `m4`
  lane): the migrate Job applied all 17 migrations; the worker `/readyz` 200 (db, migrations, kek,
  queue); the app `/readyz` 200 (db, jobs, keys `db+static`), detect 200 with the static key, 401
  without, `/api/v1/openapi.json` 404, `GET /admin/posture` **11/11 passed, enforced**; the browser
  worker ready with Chromium 140; the exports volume writable through the fsGroup, the root
  read-only (EROFS); dialling the same server as `dps-db-other` fails `SSLCertVerificationError`
  (why `DPS_PG_HOST` is the certificate's name); 0 hits of the API key, the KEK or the request's
  e-mail in any container log. The first worker run failed — `password authentication failed for
  user dps_app` — because the worker opens the API pool too: fixed (both workers hold both logins).
  The CronJob's rendered prod pod spec (same emulation, owner secret only) applied the 17 migrations
  on an empty database and ensured partitions `2026_10`…`2027_01`; an audit insert dated 2027-02-01
  then failed (`no partition of relation "dps_audit_log" found for row`, the B1 failure); a rerun the
  same day applied 0 and dropped 0; the same command with the clock at 2026-11-14 created `2027_02`
  (the insert then succeeded), and at 2027-12-02 created `2027_12`…`2028_03` and dropped `2026_10`
  (retention 13 months); 0 DSN/password/traceback lines in its output.
  S5 review r2 (B1), the same emulation with each container's cpu/memory limits, this tree's prod
  render: an "old release" Job (this tree's migrations without 045) applied 16; **without the wait**
  (control) the app container then started with `jobs: migrations pending or drifted` and
  `GET /api/v1/jobs` 503 `db_unavailable`, the release Job applied 045, and 25 s later the same app
  still answered 503 (the review's race, reproduced in containers). **With the wait**, same steps:
  the app's and the worker's `wait-for-schema` kept checking (4 checks each in 20 s, every one
  naming `045_policy_directory.sql: pending`), exited 0 one to four seconds after the release Job
  applied 045, and the containers started after them were ready at once — app `/readyz` 200 with
  `jobs: ready` and `GET /api/v1/jobs` 200, worker `/readyz` 200 (db, migrations, kek, queue). One
  verify under the wait's limits (0.5 CPU, 256 MiB): 0.9 s, 34 MiB peak. Browser image (`m4`, 19
  migrations): its wait looped on an empty schema; `docker stop` (SIGTERM) ended a waiting one in
  under a second (exit 143); its own migrations applied 19 and the wait exited 0 five seconds later;
  the browser worker then ready (`api-core`, `browser`). 0 hits of the KEK, the API key, a DSN or a
  traceback in the 13 container logs.
  Not done: a live cluster apply.

### 14.8 M5 Task 26 — the ten-minute demo: seed, automated run, console check, showcase deck (finish stage, lane `fdemo`, 2026-10-15)

**What changes for a build, a deploy or a demo.**
- The compose `demo-secrets` one-shot also copies the demo refs (the whole `default/dps/…` tree, the
  scanner's stored session included) to the demo tenant's folder `retail-banking-apac/dps/…` of the
  `dps-secrets` volume: refs are tenant-scoped (SEC-2/SEC-5), and the demo tenant's connections and
  scans use the same refs. A running stack picks it up with
  `docker compose --profile full up -d demo-secrets`.
- `python tools/demo_seed.py` (idempotent; `--base-url`, `--admin-key`, `--tenant`, `--rows 20000`,
  `--skip-sources`, `--skip-oracle`, `--reseed`, `--reset-jobs`) seeds tenant `retail-banking-apac`:
  three keys (`demo-console` every scope, `crm-uat-provisioner`, `qa-onboarding-scanner`) whose raw
  values go only to `var/demo/demo-keys.json` (mode 600, git-ignored); the three source engines
  (`tools/seed_sources.py`, seed 7); seven connections (`bank-pg`/`bank-uat`,
  `crm-oracle`/`crm-masked`, `profiles-mongo`/`profiles-uat`, `exports-minio`); the policy
  `retail-kyc` v2 (`CHG-48213`); the job group `crm-uat-refresh`, whose key material and seed exist
  only in `var/demo/group-crm-uat-refresh.json` (mode 600).
- `python tools/e2e_demo.py [--console] [--skip-scanner] [--skip-oracle]` runs the demo story S1–S11
  (S12 = the console walk) as the `demo-console` key and writes `reports/e2e-demo-report.md`, the
  `dps.e2e-demo.v1` JSON that `tools/build_deck.py` reads, and `reports/demo-screens/<page>-<theme>.png`
  (all git-ignored). Endpoints come from `DPS_BASE_URL`/`DPS_TEST_APP_URL`/`DPS_APP_PORT` and the
  demo site's counters from `DPS_TEST_DEMO_SITE`/`DPS_DEMO_SITE_PORT`, as for the smoke.
- `docs/demo/dps-showcase.pptx` is committed, built `--strict` from a complete rehearsal;
  `tests/test_deck.py` checks the committed file (twelve slides, notes = the script's Slide blocks, no
  placeholder, Keynote-safe parts and fonts). Rebuild after editing a Slide block of
  `docs/demo/DEMO-SCRIPT.md`, and after Task 28 (slide 1 prints the stack's version).

**Verified (lane `fdemo`, `--profile full`, ports 8418…):** `demo_seed.py` then
`e2e_demo.py --console` at `37ec781`: S1–S12 PASS in 3:25 (API path 3:12); 20 000-row runs on
PostgreSQL, MongoDB and Oracle under the group key (4 partitions each, verified), 3/3 pairs
consistent (500 of 500), copula companion (fidelity ≥ 0.85, exact-row matches 0, NNDR above its
target, MIA and utility reported), a scan with the demo site's non-GET, GET-form, `/logout` and
`/admin` counters unmoved while it served 175 GETs and the guard blocked 60 sends;
`tests/console/test_console_pages.py` 9 passed; `dps_llm_raw_pii_sent_total` 0. Source vs target
(counts only): `bank_uat.customers_demo`, `crm_uat.profiles_demo`, `CRM_MASKED.CUSTOMERS_DEMO`:
0 same-row survivors and 0 set-equal names, e-mails, mobiles, national ids, tax ids and notes
(customer ids 4 and shifted birth dates: domain collisions); `bank_uat.customers_synthetic`: 0
customer ids, e-mails, national ids and tax ids equal to a source value (the pack's own
identifier check: national ids and tax ids 0 in the source); its names, mobiles and birth dates meet source values only as surrogate-domain
collisions (the demo source comes from the same fictional ranges). Log and artefact scan: 0 hits of
the demo keys, the group key and seed, the bootstrap key, the KEK, the sample records and 2 000
sampled source values per personal column.

**Traps.** (1) After the test suite Oracle and MongoDB hold 200 rows: `demo_seed.py` stops with
`seed_sources.py` exit 3; run it with `--reseed`. (2) A retention job belongs to the system tenant:
`GET /jobs/{id}` answers 404 for every tenant (and `_system` is reserved); wait for its
`retention.run` audit row (`/admin/audit?tenants=*&action=retention.run`). (3) A run without
`policy` resolves its snapshot's policy name as a preset, so a snapshot discovered under a tenant
policy (`@name`) fails every run with `policy_invalid` unless the run names `@name` again (open
product gap, reported to the owner). (4) Keynote 14.4 does not open a deck through AppleScript
without a person (AppleEvent -1712): review the Keynote PDF export by hand.
