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
| `dps` package | Python 3.12 | Layer 1 engine + API; extras `fpe` and `docs` (baked in; the app needs `docs` from M1 Task 10 on), `ner` (build-arg `WITH_NER=1`), `vertex` |
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
- stage 2 `python:3.12-slim`: `tesseract-ocr` + `tesseract-ocr-eng` from apt and
  `ENV DPS_OCR_PROVIDER=auto` (M1: local OCR when no Azure endpoint is configured),
  `uv` 0.11.21 (pinned, copied from `ghcr.io/astral-sh/uv:0.11.21`),
  `uv pip install --system --no-cache -e ".[fpe,docs]"` (`.[fpe,docs,ner]` + `en_core_web_sm` when `WITH_NER=1`),
  the console dist at `/app/frontend/dist`;
- `USER 10001`, `EXPOSE 8400`, a Python `urllib` `HEALTHCHECK` on `http://127.0.0.1:8400/health`
  (15 s interval, 15 s start period);
- `ENTRYPOINT docker/entrypoint.sh` (M0: `exec "$@"`; M2 adds migrations) and
  `CMD uvicorn dps.app:create_app --factory --host 0.0.0.0 --port 8400 --no-access-log`.

Sizes on 2026-10-11 (arm64): 407 MB; with `WITH_NER=1` 775 MB. On 2026-10-12 (arm64, M1 lane: the
`docs` extra and Tesseract 5.5 with `eng`): 644 MB, of which the apt layer is 107 MB.

## 3. Run locally (compose)

```bash
docker compose up --build -d          # http://127.0.0.1:8400, key dps_local_dev_key_change_me
python tools/smoke_test.py            # waits for /health (DPS_SMOKE_WAIT_SECONDS, default 90), then 13 checks
docker compose down
```

One service, `app` (image `data-privacy-services:${DPS_IMAGE_TAG:-local}`), port
`127.0.0.1:${DPS_APP_PORT:-8400}:8400`.
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

Declared but not read in M0: `DPS_HOST`, `DPS_PORT` (the image's `CMD` fixes `0.0.0.0:8400`),
`DPS_MIN_SCORE` (the policy's `detect.min_score` applies), `DPS_APP_NAME`. The M1 document, OCR and
schema settings above are validated (and posture-checked) from M1 Task 1 on and read as the document
pipeline and routes land during M1.

Compose-only variables (read by `docker-compose.yml` and the tools, not by the app): `DPS_IMAGE_TAG`
(default `local`) and the host-port variables `DPS_APP_PORT` (8400; the others arrive with their
services, §3 "Lane-safe stacks").

## 5. Probes and ops surfaces

- `GET /health` — liveness, `{"status": "ok"}`.
- `GET /readyz` — `{"ready", "components"}` with `api-core`, `llm-gateway` (`provider`, `coin_tls`
  `verified` / `insecure`), `ner`
  (`presidio` / `patterns-only`), `fpe` (`ff1` / `det_token fallback`) and, from M1, `ocr`
  (`up` / `down` / `down:<status>` / `error` / `unconfigured` / `tesseract_missing`, with `provider`
  and `engine` — `azure-read-v3.2` or `tesseract-<major.minor>`; probed at
  startup and every `DPS_OCR_PROBE_INTERVAL_SECONDS` while OCR is configured; never required, since
  JSON and text need no OCR); **503** only when a required component is down, and only `api-core`
  is required (it turns false on shutdown).
- `GET /metrics` — Prometheus: `dps_requests_total{route,outcome}`,
  `dps_request_seconds{route}`, `dps_findings_total{entity_type,detector}`,
  `dps_transforms_total{mode,method}`, `dps_llm_calls_total{provider,purpose,outcome}`,
  `dps_verification_failures_total{mode}` and **`dps_llm_raw_pii_sent_total`, which must stay 0**;
  M1 adds `dps_document_pages_total{format,source}`, `dps_redacted_boxes_total`,
  `dps_ocr_calls_total{engine,outcome}` (engine `azure-read-v3.2` or `tesseract`),
  `dps_ocr_seconds` and `dps_llm_label_fields_total{outcome}` (T3 labelling: `labelled`,
  `unlabelled`, `skipped` over the per-call cap, `blocked` by the leak guard, `failed`; the call
  itself is `dps_llm_calls_total{purpose="label"}`).
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
- `GET /docs` (Swagger UI) and `GET /api/v1/openapi.json`. Probe and metrics routes are not in the schema.

## 6. Security posture

- **Posture guard** (`DPS_ENV=staging|prod`, checked before the app is built, the process exits on a
  violation): auth on, bootstrap key unset, `DPS_API_KEYS` set, provider not `stub`, `stellar` needs
  `DPS_SSL_CERT_FILE` and both URLs, Stellar/COIN URLs `https`, `DPS_COIN_TLS_INSECURE=true` only
  with `DPS_ALLOW_INSECURE_COIN_MINT=true`, audit on, and with `DPS_OCR_PROVIDER=azure` a
  `DPS_AZURE_VISION_KEY` and an `https` `DPS_AZURE_VISION_ENDPOINT` (M1). Messages name the setting,
  never its value.
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

`DPS_API_KEYS`, `DPS_COIN_CLIENT_ID`, `DPS_COIN_CLIENT_SECRET`, `DPS_COIN_SCOPE`, and from M1
`DPS_AZURE_VISION_KEY` — mount them and use the `_FILE` form. Nothing else. `DPS_BOOTSTRAP_API_KEY` is a local demo literal and must never be
set outside a laptop. DPS never stores customer data, reveal keys or synthesis seeds.

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
