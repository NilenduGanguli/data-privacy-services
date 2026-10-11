# HANDOFF — data-privacy-services (DPS)

Last verified: 2026-10-11 at `4c86dc7` (code; the docs commit on top changes no code) · gates:
`pytest -q` (434 passed, 1 skipped: the Presidio test without the `ner` extra), `ruff check dps tests tools`,
`mypy dps`, `cd frontend && npm run build`, `docker compose up --build -d` + `python tools/smoke_test.py`
(13/13). Also checked that day: the `WITH_NER=1` image (13/13, `ner=ok`), a `linux/amd64` build, and
the stack with `DPS_FPE_ENABLED=false` (13/13, `det_token` fallbacks reported).

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
docker build -t data-privacy-services:local .                                      # what compose builds
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

One service, `app` (image `data-privacy-services:local`), port `127.0.0.1:${DPS_APP_PORT:-8400}:8400`.
Hardening: `read_only: true` with a 256 MB `tmpfs` on `/tmp`, `cap_drop: [ALL]`,
`no-new-privileges`, `mem_limit: 1536m`; labels `app=data-privacy-services`, `component=api`,
`env=local`. Compose sets `DPS_ENV=local`, the bootstrap key, `DPS_DEFAULT_LOCALE=en_IN`,
`DPS_FPE_ENABLED=true`, `DPS_LLM_PROVIDER=stub` and passes the Stellar/COIN variables through (each
overridable from the shell; `DPS_COIN_TLS_INSECURE` defaults to `false`). The smoke test reads `DPS_BASE_URL` or `DPS_TEST_APP_URL` (default
`http://127.0.0.1:${DPS_APP_PORT:-8400}`) and `DPS_API_KEY` (default the local demo key).

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

Declared but not read in M0: `DPS_HOST`, `DPS_PORT` (the image's `CMD` fixes `0.0.0.0:8400`),
`DPS_MIN_SCORE` (the policy's `detect.min_score` applies), `DPS_APP_NAME`.

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
the `_FILE` form. Nothing else. `DPS_BOOTSTRAP_API_KEY` is a local demo literal and must never be
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
- Port 8400 busy: `DPS_APP_PORT=8401` for both `docker compose up` and `tools/smoke_test.py`.

## 9. Next milestones

M1 documents → M2 jobs + PostgreSQL (adds Postgres, worker, migrations) → M3 Oracle + MongoDB → M4 UI
scanner (browser worker image) → M5 hardening + showcase. Each adds a section here.
