# HANDOFF — data-privacy-services (DPS)

Last verified: 2026-10-12 at `629f4db` = tag `v0.1.0-m0` (`48d9425`) + the post-M0 fixes (SEC-1 COIN
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
| `data-privacy-services` image | container | FastAPI app + compiled console; one process, port **8400**; non-root uid **10001** |
| `dps` package | Python 3.12 | Layer 1 engine + API; extras `fpe` (baked in), `ner` (build-arg `WITH_NER=1`), `vertex` |
| `frontend/dist` | built inside the image | never committed (`.gitignore`, `.dockerignore`) |
| `tools/smoke_test.py` | script | 13 end-to-end checks against a running stack; prints verdicts, never bodies |

## 2. Build

```bash
docker build -t data-privacy-services:local .                                      # what compose builds (tag = ${DPS_IMAGE_TAG:-local})
docker build --platform linux/amd64 -t <registry>/data-privacy-services:<semver> .   # amd64 from a Mac
docker build --build-arg WITH_NER=1 -t <registry>/data-privacy-services:<semver>-ner .
```

Tag semver + git sha. No database, no volumes in M0. The image:

- stage 1 `node:22-slim`: `npm ci` (lockfile only, no `npm install` fallback) + `npm run build`;
- stage 2 `python:3.12-slim`: `uv` 0.11.21 (pinned, copied from `ghcr.io/astral-sh/uv:0.11.21`),
  `uv pip install --system --no-cache -e ".[fpe]"` (`.[fpe,ner]` + `en_core_web_sm` when `WITH_NER=1`),
  the console dist at `/app/frontend/dist`;
- `USER 10001`, `EXPOSE 8400`, a Python `urllib` `HEALTHCHECK` on `http://127.0.0.1:8400/health`
  (15 s interval, 15 s start period);
- `ENTRYPOINT docker/entrypoint.sh` (M0: `exec "$@"`; M2 adds migrations) and
  `CMD uvicorn dps.app:create_app --factory --host 0.0.0.0 --port 8400 --no-access-log`.

Sizes on 2026-10-11 (arm64): 407 MB; with `WITH_NER=1` 775 MB.

## 3. Run locally (compose)

```bash
docker compose up --build -d          # http://127.0.0.1:8400, key dps_local_dev_key_change_me
python tools/smoke_test.py            # waits for /health (DPS_SMOKE_WAIT_SECONDS, default 90), then 13 checks
docker compose down
```

Services (host ports bind `127.0.0.1` only):

| Service | Image | Host port → container | Notes |
|---|---|---|---|
| `app` | `data-privacy-services:${DPS_IMAGE_TAG:-local}` | `${DPS_APP_PORT:-8400}` → 8400 | console + API |
| `dps-db` (M2) | `postgres:16-alpine` | `${DPS_DB_PORT:-5441}` → 5432 | metadata DB; `docker/dps-db/initdb/01_roles.sql` creates the contract roles (`dps_owner`, `dps_app_rw`, `dps_worker`, `dps_app`, `dps_worker_login`; local passwords = role name) and the `dps` database (owner `dps_owner`, timezone UTC) on first start; volume `dps-db-data`; superuser `postgres`/`postgres` (local only); healthy once `pg_isready -h 127.0.0.1 -d dps` answers |
| `minio` (M2, profiles `sources`/`full`) | `minio/minio:latest` | `${DPS_MINIO_PORT:-9010}` → 9000, `${DPS_MINIO_CONSOLE_PORT:-9011}` → 9001 (console) | artefacts and `files` exports (`DPS_ARTEFACT_STORE=s3`); root user `minioadmin`/`minioadmin` (local only); volume `minio-data`; healthcheck `curl /minio/health/live` (the cached image ships curl) |
| `minio-init` (M2, profiles `sources`/`full`) | `minio/mc:latest` | — | one-shot after `minio` is healthy: creates buckets `dps-artefacts` and `dps-exports`, exits 0 |

`docker compose up -d dps-db` starts the metadata database alone (the `db` tests need nothing else).
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
(M2 owns 001–019). Against the compose database:
`DPS_PG_HOST=127.0.0.1 DPS_PG_PORT=${DPS_DB_PORT:-5441} DPS_PG_PASSWORD=dps_app DPS_PG_MIGRATION_USER=dps_owner DPS_PG_MIGRATION_PASSWORD=dps_owner python -m dps.migrate`.
Hardening: `read_only: true` with a 256 MB `tmpfs` on `/tmp`, `cap_drop: [ALL]`,
`no-new-privileges`, `mem_limit: 1536m`; labels `app=data-privacy-services`, `component=api`,
`env=local`. Compose sets `DPS_ENV=local`, the bootstrap key, `DPS_DEFAULT_LOCALE=en_IN`,
`DPS_FPE_ENABLED=true`, `DPS_LLM_PROVIDER=stub` and passes the Stellar/COIN variables through (each
overridable from the shell; `DPS_COIN_TLS_INSECURE` defaults to `false`). The smoke test reads `DPS_BASE_URL` or `DPS_TEST_APP_URL` (default
`http://127.0.0.1:${DPS_APP_PORT:-8400}`) and `DPS_API_KEY` (default the local demo key).

**Lane-safe stacks** (contract §0, amendment 2026-10-12). Parallel milestone lanes each run their own
stack on this host, so `docker-compose.yml` fixes neither a host port nor an image tag:

- every host port is `127.0.0.1:${DPS_<NAME>_PORT:-<contract port>}:<container port>`: `DPS_APP_PORT`
  8400 today; as services arrive, `DPS_DB_PORT` 5441, `DPS_SOURCE_PG_PORT` 5442, `DPS_MINIO_PORT` 9010,
  `DPS_MINIO_CONSOLE_PORT` 9011, `DPS_ORACLE_PORT` 1522, `DPS_MONGO_PORT` 27018, `DPS_OCR_PORT` 5016,
  `DPS_DEMO_SITE_PORT` 8410, `DPS_TOKENIZER_MOCK_PORT` 8420 (worker metrics ports likewise if published);
- every image compose builds is tagged `${DPS_IMAGE_TAG:-local}` (`data-privacy-services`, later
  `data-privacy-services-browser`; the OCR mock `${DPS_IMAGE_TAG:-latest}`);
- tests and tools read endpoints only from `DPS_TEST_*` (contract §12.2), whose defaults follow the same
  port variables; `tools/smoke_test.py` reads `DPS_TEST_APP_URL` / `DPS_APP_PORT`;
- give each lane its own `COMPOSE_PROJECT_NAME` so containers, networks and volumes stay apart:

```bash
COMPOSE_PROJECT_NAME=dps-m2 DPS_IMAGE_TAG=m2 DPS_APP_PORT=8401 docker compose up --build -d
DPS_APP_PORT=8401 python tools/smoke_test.py
COMPOSE_PROJECT_NAME=dps-m2 docker compose down
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
| `DPS_MAX_PAYLOAD_MB` / `DPS_MAX_TEXT_CHARS` / `DPS_MAX_ITEMS` | 8 / 1,000,000 / 10,000 | 413 beyond (body bytes by header or while streaming; text characters; JSON leaf values) |
| `DPS_RATE_LIMIT_ENABLED` / `_RPS` / `_BURST` | `true` / 50 / 100 | token bucket per API key, per process; 429 + `Retry-After` |
| `DPS_AUDIT_ENABLED` | `true` | `false` only locally; refused in staging/prod |

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

Declared but not read in M0: `DPS_HOST`, `DPS_PORT` (the image's `CMD` fixes `0.0.0.0:8400`),
`DPS_MIN_SCORE` (the policy's `detect.min_score` applies), `DPS_APP_NAME`.

Compose-only variables (read by `docker-compose.yml` and the tools, not by the app): `DPS_IMAGE_TAG`
(default `local`) and the host-port variables `DPS_APP_PORT` (8400), `DPS_DB_PORT` (5441),
`DPS_MINIO_PORT` (9010), `DPS_MINIO_CONSOLE_PORT` (9011; the others arrive with their services, §3
"Lane-safe stacks").

## 5. Probes and ops surfaces

- `GET /health` — liveness, `{"status": "ok"}`.
- `GET /readyz` — `{"ready", "components"}` with `api-core`, `llm-gateway` (`provider`, `coin_tls`
  `verified` / `insecure`), `ner`
  (`presidio` / `patterns-only`), `fpe` (`ff1` / `det_token fallback`); **503** only when a required
  component is down, and only `api-core` is required in M0 (it turns false on shutdown).
- `GET /metrics` — Prometheus: `dps_requests_total{route,outcome}`,
  `dps_request_seconds{route}`, `dps_findings_total{entity_type,detector}`,
  `dps_transforms_total{mode,method}`, `dps_llm_calls_total{provider,purpose,outcome}`,
  `dps_verification_failures_total{mode}` and **`dps_llm_raw_pii_sent_total`, which must stay 0**.
- Audit: one `audit` log line per engine request, rejections included (trace id, route, tenant, key
  id, mode, policy label, outcome, counts, methods, verification, duration — never a value or a key).
  Logs go to stdout through an allow-list of field names; outside `local` they are JSON.
- `GET /docs` (Swagger UI) and `GET /api/v1/openapi.json`. Probe and metrics routes are not in the schema.

## 6. Security posture

- **Posture guard** (`DPS_ENV=staging|prod`, checked before the app is built, the process exits on a
  violation): auth on, bootstrap key unset, `DPS_API_KEYS` set, provider not `stub`, `stellar` needs
  `DPS_SSL_CERT_FILE` and both URLs, Stellar/COIN URLs `https`, `DPS_COIN_TLS_INSECURE=true` only
  with `DPS_ALLOW_INSECURE_COIN_MINT=true`, audit on. Messages name the setting, never its value.
  With `DPS_PG_HOST` set (M2) it also requires: the job KEK from `DPS_JOB_KEK_FILE` (a literal
  `DPS_JOB_KEK` env var is refused), `DPS_INLINE_SECRETS_ALLOWED=false`, `DPS_RLS_ENABLED=true`,
  `DPS_MIGRATIONS_MODE=verify`, `DPS_AUDIT_STRICT=true`, `DPS_PG_SSL_ROOT_CERT` set and
  `DPS_EMBEDDED_WORKER=false`.
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
- CORS: any origin, methods `GET POST OPTIONS`, headers `Content-Type X-API-KEY X-DPS-Tenant
  X-Request-ID` (keys travel in headers, not cookies; to be narrowed in M5).
- Error bodies never quote the request: 422s list `{type, loc, msg}` only, unexpected errors are a
  500 `internal error` with only the exception class logged.

## 7. Secrets (names only)

`DPS_API_KEYS`, `DPS_COIN_CLIENT_ID`, `DPS_COIN_CLIENT_SECRET`, `DPS_COIN_SCOPE` — mount them and use
the `_FILE` form. M2 adds `DPS_JOB_KEK` (`_FILE` only in staging/prod), `DPS_PG_PASSWORD`,
`DPS_PG_WORKER_PASSWORD`, `DPS_PG_MIGRATION_PASSWORD` and `DPS_S3_SECRET_ACCESS_KEY`. Nothing else. `DPS_BOOTSTRAP_API_KEY` is a local demo literal and must never be
set outside a laptop. DPS never stores customer data, reveal keys or synthesis seeds.

Connection credentials (M2, `dps/connections.py`): a connection's `secret_ref` names a directory
`DPS_SECRET_DIR/<ref>/` from which only these files are read: `username`, `password`, `dsn`
(Oracle), `uri` (Mongo), `access_key_id` + `secret_access_key` (S3), `storage_state.json` (M4) and
`ca.pem` (a source's CA bundle). Inline credentials (dev only) are sealed with `DPS_JOB_KEK` into
`dps_connection.secret_enc` and stop resolving after `DPS_INLINE_SECRET_TTL_HOURS`. A `secret_ref`
is not tenant-scoped: any tenant with the `jobs` scope can name any mounted reference, so mount only
the references the deployment's tenants may use (open issue, see the M2 build report).

## 8. Traps

- `uvicorn dps.app:create_app --factory` — there is no module-level `app`.
- The image installs `dps` **editable** on purpose: `dps/app.py` serves `<source root>/frontend/dist`.
  A wheel install into site-packages starts without the console (`frontend_disabled` in the log).
- Routes in the console are one segment deep (`base: './'`).
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
  parameters).
- Port 8400 busy: `DPS_APP_PORT=8401` for both `docker compose up` and `tools/smoke_test.py` (see
  "Lane-safe stacks", §3).
- Synthesis under a `strict` policy is still refused, rarely, when a reshaped identifier (a keyed
  derangement such as `CUSTOMER_ID`) holds another value of the same record by construction:
  `CUST-xx0928` next to a card expiry `09/28`. The derangement cannot be re-drawn without breaking
  joins, so the sweep fails closed (422). With the Playground JSON sample it happens for about one seed
  in 3,000 to 5,000; retry without the seed (or with another one). Every other surrogate is re-drawn
  when it would hold another value of the record or a part of its names.

## 9. Next milestones

M1 documents → M2 jobs + PostgreSQL (adds Postgres, worker, migrations) → M3 Oracle + MongoDB → M4 UI
scanner (browser worker image) → M5 hardening + showcase. Each adds a section here.
