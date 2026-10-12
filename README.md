# Data Privacy Services (DPS)

Data obfuscation and synthetic data generation for the enterprise — one Privacy Engine, three entry
points (API, database connectors, UI scanner), two outcomes (reversible obfuscation, synthetic data),
measurable privacy, and a hard rule that **no LLM ever sees raw PII**.

**Status:** M2 shipped (tag `v0.1.0-m2`) on top of M0 — the Layer 1 API (`/api/v1/detect`, `/obfuscate`,
`/synthesize`, `/reveal`, policy tooling) and the Privacy Console (Overview, Playground), plus the job
platform: connect a PostgreSQL source, discover and review its columns, and run partitioned,
checkpointed, resumable masking jobs in four write modes (copy to a table, export files, in place,
append) that end with a hashed evidence pack — from the API or the console's Data sources & jobs
pages.

| Read | For |
|---|---|
| [`docs/specs/2026-10-11-data-privacy-services-design.md`](docs/specs/2026-10-11-data-privacy-services-design.md) | the design (§12 lists the open decisions) |
| [`HANDOFF.md`](HANDOFF.md) | build, run, configuration, probes, traps |
| [`docs/technical/api-m0.md`](docs/technical/api-m0.md) | the Layer 1 API reference |
| [`docs/technical/api-m2.md`](docs/technical/api-m2.md) | the jobs and PostgreSQL API (routes, error codes, state machine, evidence pack, measured throughput) |
| [`docs/design/mockups/index.html`](docs/design/mockups/index.html) | the console mockups (M0 builds Overview and Playground; M2 the jobs pages 07, 03, 06, 10 and the evidence list of 09) |
| [`docs/superpowers/plans/`](docs/superpowers/plans/) | the milestone plans (M0–M5) and the M1–M5 contract |

## Run it

```bash
docker compose up --build -d         # console + API on http://127.0.0.1:8400 (key: dps_local_dev_key_change_me)
DPS_SMOKE_M2=off python tools/smoke_test.py   # 15 Layer 1 checks (the 9 job checks need the full stack below)
docker compose down
```

The full stack with the job platform (M2):

```bash
docker compose --env-file docker/compose.sources.env --profile sources up -d --build   # app, worker, metadata DB, demo source, MinIO
python tools/smoke_test.py                                                               # L1 + M2 checks
python tools/demo_m2.py                                                                  # 10-table UAT copy, in place, append
```

Poll `docker compose ps` until `app`, `worker`, `dps-db`, `source-pg` and `minio` are healthy and the
one-shots (`migrate`, `minio-init`, `demo-secrets`, `seed-sources`) exited 0 (`up --wait` fails on the
one-shots). A stack whose ports and image tag live in `.env` passes it first:
`docker compose --env-file .env --env-file docker/compose.sources.env …` (see `HANDOFF.md` §3).
Then open **Data sources & jobs → New job** in the console.

Port 8400 (or the metadata database's 5441) taken? `DPS_APP_PORT=8401 DPS_DB_PORT=5451 docker compose up -d`
and `DPS_APP_PORT=8401 DPS_SMOKE_M2=off python tools/smoke_test.py`.
Open the console, paste the key into the header, and try the Playground: Obfuscate → Reveal with key,
or Synthesize.

## Develop

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate && uv pip install -e ".[dev,fpe,db,connectors,e2e]"
ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q
(cd frontend && npm ci && npm run typecheck && npm test && npm run build)
DPS_AUTH_ENABLED=false DPS_LLM_PROVIDER=stub uvicorn dps.app:create_app --factory --port 8400
cd frontend && npm run dev                       # console on :5174, API calls proxied to :8400
```

Services for the integration tests: `docker compose up -d dps-db` (marker `db`) and `docker compose
--profile sources up -d source-pg minio minio-init` (`pg_source`, `s3`), or, without Docker,
`tools/scratch_pg.sh start all` (Homebrew PostgreSQL 16 on 5441/5442). Markers: `db`, `pg_source`,
`s3`, `slow` (resume-after-kill, about 90 s), `browser` (the console check; `python -m playwright
install chromium` once). Without a service its tests skip; `DPS_TEST_REQUIRE_SERVICES=1` makes them
fail instead. The milestone gate with the full stack up:

```bash
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m slow tests/connectors/test_resume_after_kill.py
DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/console/test_console_m2.py
```

`npm run build` writes `frontend/dist`, which the API process serves at `/` when it exists.
Optional extras: `ner` (Presidio + spaCy, higher recall on names and places), `fpe` (FF1
format-preserving encryption, used only with `DPS_FPE_ENABLED=true`), `vertex` (laptop path to
Gemini), `db` (asyncpg: metadata database and PostgreSQL connector), `connectors` (sqlglot, pyarrow,
boto3), `e2e` (Playwright 1.55.0 for the console check). Production goes through the Stellar gateway — see `HANDOFF.md`.

## How it works

detect (structural key names + patterns/checksums + optional NER + client declarations) → plan
(policy → one method per finding, with a reason) → transform (AES-SIV tokens, FF1, keyed date shift,
surrogates…) → verify (no original value survives in the output) → report (counts, methods,
fallbacks, verification — never a value). Keys and synthesis seeds are returned to the caller and
never stored. Synthetic text goes through the LLM as typed placeholders only.

Jobs (M2): connect (read-only transactions, least-privilege checks) → discover (sample, classify
each column, propose a snapshot) → review (accept or override per column) → run (key-space partitions
claimed by workers from a Postgres queue, transformed by the same engine, written in committed batches
with a write ledger and a checkpoint after each, so a killed worker's partition resumes without
writing a row twice) → evidence (a hashed, printable pack of counts, hashes, methods and key ids).
