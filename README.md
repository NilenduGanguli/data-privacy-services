# Data Privacy Services (DPS)

Data obfuscation and synthetic data generation for the enterprise — one Privacy Engine, three entry
points (API, database connectors, UI scanner), two outcomes (reversible obfuscation, synthetic data),
measurable privacy, and a hard rule that **no LLM ever sees raw PII**.

**Status:** M0, M1 (v0.2.0) and M2 (`v0.1.0-m2`) integrated on `main`. **Layer 1 is complete**: the
JSON/text API of M0 (`/api/v1/detect`, `/obfuscate`, `/synthesize`, `/reveal`, policy tooling) plus
**documents** (PDF text layer, scanned pages and images through the Azure Read v3.2 contract or local
Tesseract, DOCX, TXT, CSV; per-page findings with boxes; a true redacted PDF verified by structure and
an OCR re-read), **synthesis from a schema, DDL or sample** through a reusable generator spec, the
entity taxonomy and a recognizer test, NDJSON streaming, `Idempotency-Key`, and a measured detector
evaluation. **Layer 2 runs on PostgreSQL**: connect a source, discover and review its columns, and run
partitioned, checkpointed, resumable masking jobs in four write modes (copy to a table, export files,
in place, append) that end with a hashed evidence pack. The Privacy Console has the Playground (with
the **Document** tab), **Policies**, and the **Data sources & jobs** pages (jobs list, new-job wizard,
job detail, evidence pack, reports). All offline in compose.

| Read | For |
|---|---|
| [`docs/specs/2026-10-11-data-privacy-services-design.md`](docs/specs/2026-10-11-data-privacy-services-design.md) | the design (§12 lists the open decisions) |
| [`HANDOFF.md`](HANDOFF.md) | build, run, configuration, probes, traps |
| [`docs/technical/api-m0.md`](docs/technical/api-m0.md) | the Layer 1 API reference (JSON and text) |
| [`docs/technical/api-m1.md`](docs/technical/api-m1.md) | the M1 additions: documents, schema synthesis, taxonomy, NDJSON, `Idempotency-Key`, OCR providers, T3 labelling |
| [`docs/technical/api-m2.md`](docs/technical/api-m2.md) | the jobs and PostgreSQL API (routes, error codes, state machine, evidence pack, measured throughput) |
| [`docs/technical/detector-eval-m1.md`](docs/technical/detector-eval-m1.md) | detector precision and recall per entity type on the synthetic labelled corpus |
| [`docs/design/mockups/index.html`](docs/design/mockups/index.html) | the console mockups (built so far: Overview, Playground with the Document tab, Policies 05, documents 08, and the jobs pages 07, 03, 06, 10 with the evidence list of 09) |
| [`docs/superpowers/plans/`](docs/superpowers/plans/) | the milestone plans (M0–M5) and the M1–M5 contract |

## Run it

```bash
docker compose --env-file docker/compose.sources.env --profile docs --profile sources up -d --build
                                     # console + API on http://127.0.0.1:8400 (key: dps_local_dev_key_change_me),
                                     # worker, metadata DB, OCR mock (5016), demo source (5442), MinIO (9010)
python tools/smoke_test.py           # Layer 1 + documents/OCR + job-platform checks (needs the dev install below for httpx)
python tools/demo_m2.py              # 10-table UAT copy, in place, append
docker compose --profile docs --profile sources down
```

Poll `docker compose ps` until `app`, `worker`, `dps-db`, `azure-ocr-mock`, `source-pg` and `minio`
are healthy and the one-shots (`migrate`, `minio-init`, `demo-secrets`, `seed-sources`) exited 0
(`up --wait` fails on the one-shots). A stack whose ports and image tag live in `.env` passes it first:
`docker compose --env-file .env --env-file docker/compose.sources.env …` (see `HANDOFF.md` §3).

Smaller stacks: plain `docker compose up --build -d` runs the app, worker and metadata DB (then
`DPS_SMOKE_M2=off python tools/smoke_test.py`: the job checks need the `sources` profile's demo source;
the OCR checks skip without `--profile docs`); `--profile docs` alone adds the OCR mock. Without
`--profile docs` JSON, text, text PDFs, DOCX and CSV work, while scans, images and redacted PDFs need
OCR (see `HANDOFF.md` §10). Ports taken? `DPS_APP_PORT=8401 DPS_DB_PORT=5451 DPS_OCR_PORT=5017 docker
compose …` and `DPS_APP_PORT=8401 python tools/smoke_test.py`.

Open the console, paste the key into the header, and try:

- **Playground → Document → Load demo KYC scan → Redact**: OCR reads the synthetic scan, the
  findings are boxed on each page, the redacted PDF (image-only, re-read with OCR) downloads, and
  *Reveal text with key* restores the original text from the obfuscated extraction.
- **Playground → JSON / Text**: Obfuscate → Reveal with key, or Synthesize.
- **Policies**: the six presets, the treatment per entity type (edit a method to start a session
  draft), a custom recognizer tested on the engine, and the plan preview.
- **Data sources & jobs → New job**: connect `bank-pg-ro`, select and count rows, discover and review
  the columns, choose a write mode, run, then open the job and its evidence pack.

## Develop

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate && uv pip install -e ".[dev,fpe,docs,db,connectors,uitest,e2e]"
ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q
(cd frontend && npm ci && npm run typecheck && npm test && npm run build)
DPS_AUTH_ENABLED=false DPS_LLM_PROVIDER=stub uvicorn dps.app:create_app --factory --port 8400
cd frontend && npm run dev                       # console on :5174, API calls proxied to :${DPS_APP_PORT:-8400}
python tools/make_demo_docs.py                   # regenerate the committed synthetic demo documents
```

`npm test` runs both console suites: `node --test` over `frontend/tests/*.test.ts` (M1; Node >= 22.6)
and Vitest over `frontend/src/**/*.test.ts` (M2).

Services for the integration tests: `docker compose up -d dps-db` (marker `db`), `docker compose
--profile sources up -d source-pg minio minio-init` (`pg_source`, `s3`) and `docker compose --profile
docs up -d azure-ocr-mock` (`ocr`), or, without Docker, `tools/scratch_pg.sh start all` (Homebrew
PostgreSQL 16 on 5441/5442). Markers: `docs` (the `[docs]` extra), `ocr`, `tesseract` (a host
`tesseract` binary), `db`, `pg_source`, `s3`, `slow` (resume-after-kill, about 90 s), `browser` (the
console check; `python -m playwright install chromium` once). Without a service its tests skip;
`DPS_TEST_REQUIRE_SERVICES=1` makes them fail instead. The milestone gates with the full stack up:

```bash
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "docs or ocr"
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m slow tests/connectors/test_resume_after_kill.py
DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/console/test_console_m2.py
```

`npm run build` writes `frontend/dist`, which the API process serves at `/` when it exists.
`python tools/console_check.py` (Playwright 1.55.0, the `uitest` extra) drives the Document tab and
the Policies page against a running stack and writes screenshots beside the mockups'.
`demo/documents/` holds synthetic KYC documents in every supported format (text and scanned PDF,
PNG, DOCX, TXT, CSV); the console's "Load demo KYC scan" serves `frontend/public/demo/kyc-scanned.pdf`.
Optional extras: `ner` (Presidio + spaCy, higher recall on names and places), `fpe` (FF1
format-preserving encryption, used only with `DPS_FPE_ENABLED=true`), `docs` (M1 documents:
pypdf, pdfplumber, pypdfium2, reportlab, python-docx, Pillow), `db` (asyncpg: metadata database and
PostgreSQL connector), `connectors` (sqlglot, pyarrow, boto3), `uitest` / `e2e` (Playwright 1.55.0
for the M1 and M2 console checks), `vertex` (laptop path to Gemini). Production goes through the
Stellar gateway — see `HANDOFF.md`.

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
