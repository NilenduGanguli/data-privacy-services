# Data Privacy Services (DPS)

Data obfuscation and synthetic data generation for the enterprise — one Privacy Engine, three entry
points (API, database connectors, UI scanner), two outcomes (reversible obfuscation, synthetic data),
measurable privacy, and a hard rule that **no LLM ever sees raw PII**.

**Status:** v1.0.0 — M0–M5 shipped. What each milestone delivered and the known limitations:
[`CHANGELOG.md`](CHANGELOG.md).

- **Layer 1 — API and documents.** `/api/v1/detect`, `/obfuscate`, `/synthesize`, `/reveal` for JSON
  and text (NDJSON streams, `Idempotency-Key`), documents (PDF text layers, scans and images through
  OCR, DOCX, TXT, CSV) with a true redacted PDF, synthesis from a schema, DDL or sample, and the bank's
  own tokenization service through `external_tokenize`.
- **Layer 2 — database connectors.** PostgreSQL, Oracle and MongoDB: connect, discover and review
  every column, then run partitioned, resumable jobs in four write modes (copy to a table, export
  files, in place, append), keep ids consistent across systems with one caller-held key, and
  synthesize tables with a Gaussian copula whose fidelity and privacy are measured before anything
  is written. Every run ends with a hashed evidence pack (JSON, HTML, PDF).
- **Layer 3 — UI scanner.** Crawls a registered UAT site, classifies its fields, fills them with
  synthetic or obfuscated personas side by side with the original, and never submits anything.
- **Privacy Console** for all of it, plus Overview, Policies (drafts and immutable versions),
  Reports & audit and Admin (keys, tenants, posture); keys and seeds go back to the caller and are
  never stored.

| Read | For |
|---|---|
| [`docs/specs/2026-10-11-data-privacy-services-design.md`](docs/specs/2026-10-11-data-privacy-services-design.md) | the design (§12 records the decisions D1–D14) |
| [`HANDOFF.md`](HANDOFF.md) | build, configuration (every `DPS_*` setting), local stack, secrets, OpenShift, runbooks, probes, traps, gates |
| [`docs/technical/api-m0.md`](docs/technical/api-m0.md) | Layer 1 API (JSON and text) |
| [`docs/technical/api-m1.md`](docs/technical/api-m1.md) | documents, schema synthesis, taxonomy, NDJSON, `Idempotency-Key`, OCR, T3 labelling |
| [`docs/technical/api-m2.md`](docs/technical/api-m2.md) | connections, jobs, snapshots, job groups, evidence, PostgreSQL |
| [`docs/technical/api-m3.md`](docs/technical/api-m3.md) | Oracle and MongoDB, the cross-system consistency check |
| [`docs/technical/api-m4.md`](docs/technical/api-m4.md) | the UI scanner: scans, pages, inventory, export |
| [`docs/technical/api-m5.md`](docs/technical/api-m5.md) | admin, reports, the policy store, tabular synthesis, `external_tokenize` |
| [`docs/demo/DEMO-SCRIPT.md`](docs/demo/DEMO-SCRIPT.md) | the ten-minute demo, and its deck `docs/demo/dps-showcase.pptx` |
| [`docs/security/2026-10-11-m5-security-review.md`](docs/security/2026-10-11-m5-security-review.md) | the security review: every finding, fix and test |
| [`docs/technical/detector-eval-m1.md`](docs/technical/detector-eval-m1.md) | detector precision and recall per entity type |
| [`docs/design/mockups/index.html`](docs/design/mockups/index.html) | the console mockups |
| [`docs/superpowers/plans/`](docs/superpowers/plans/) | the milestone plans and the M1–M5 contract |

## Run it

```bash
docker compose --env-file docker/compose.sources.env --profile full up --build -d
                                     # console + API on http://127.0.0.1:8400 (key: dps_local_dev_key_change_me,
                                     # a local demo literal), worker, metadata DB, OCR mock, demo PostgreSQL,
                                     # Oracle Free (~2.7 GB, 1-3 min first start), MongoDB, MinIO, tokenizer mock
curl -fsS 127.0.0.1:8400/readyz      # poll until "ready": true (never `docker compose up --wait`)
python tools/seed_sources.py --target all --rows 20000 --seed 7   # one customer space in PG, Oracle, MongoDB
python tools/smoke_test.py --m3      # end-to-end checks; verdicts only, never a value
python tools/demo_seed.py            # tenant, keys, connections, policies, the demo job group
python tools/e2e_demo.py --console   # the ten-minute demo end to end, with console screenshots
docker compose --profile full down   # -v for a clean slate
```

Smaller stacks: `docker compose up --build -d` (app, worker, metadata DB) or one profile at a time
(`docs`, `sources`, `oracle`, `tokenizer`, `scanner`); ports taken by another stack: set
`DPS_APP_PORT`, `DPS_DB_PORT`, … (every host port is a variable, `HANDOFF.md` §4).

Open the console, paste the key into the header, and try:

- **Playground**: a KYC record → findings → Obfuscate → Reveal with the key → Synthetic; the
  **Document** tab: load the demo scan → Redact → download the redacted PDF.
- **Policies**: presets and tenant policies, the treatment per entity type, a custom recognizer
  tested on the engine, the plan preview, versions with their diff.
- **Data sources & jobs → New job**: an Oracle, MongoDB or PostgreSQL source → discover → review →
  target and write mode → run; the job page, its evidence pack and the job groups' consistency check.
- **UI scanner**: scan the demo onboarding site, flip the parallel view between synthetic and
  obfuscated, export the inventory — "POSTs left: 0".
- **Reports & audit** and **Admin**: evidence PDFs, the audit trail, keys (shown once), tenants and
  the posture checks.

## Scan a web front end (M4)

```bash
docker compose --profile scanner up -d --build   # + browser-worker (Playwright 1.55.0 image) and the demo
                                                  # onboarding site (127.0.0.1:8410, in-network demo-site:8410)
docker compose --profile scanner ps               # poll until app, browser-worker and demo-site are healthy
DPS_SMOKE_M2=off DPS_SMOKE_SCANNER=1 python tools/smoke_test.py   # Layer 1 checks + 11 scanner checks
docker compose --profile scanner down
```

Open the console at `/scanner/new`, paste the key, and scan `http://demo-site:8410/apply` with the
session `dps/ui/demo-onboarding` (the demo site's login, copied by `demo-secrets` from `docker/secrets/default/…`):
the crawl map and navigation log fill in live; the scan detail shows each page's original and
annotated screenshots, the **parallel view** (flip synthetic ↔ obfuscated), the field inventory with
risk flags, the masked captures, the persona/field exports and the evidence pack. The scan's key and
synthesis seed are shown once. Only registered hosts can be scanned (`DPS_SCAN_REGISTERED_HOSTS`
locally: `localhost,127.0.0.1,demo-site`), and the demo site's `GET /__received` counts every
non-GET request and form submission it ever received — it stays the same through any scan.
Settings: `HANDOFF.md` §3 (the generated table); image §2, ports §4, traps §11 and the M4 build record
§14.2; API: `docs/technical/api-m4.md`.

## Develop

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -e ".[dev,fpe,docs,db,connectors,tabular,scanner,uitest,e2e]"   # + ner, vertex, synth-ml as needed
python -m playwright install chromium     # once: Chromium 1187 for the scanner and console tests
ruff format --check dps tests tools && ruff check dps tests tools && mypy dps && pytest -q
python tools/settings_table.py --check   # HANDOFF's settings table is current
(cd frontend && npm ci && npm run typecheck && npm test && npm run build)
DPS_AUTH_ENABLED=false DPS_LLM_PROVIDER=stub uvicorn dps.app:create_app --factory --port 8400
cd frontend && npm run dev            # console on :5174, API calls proxied to :${DPS_APP_PORT:-8400}
```

Integration tests need their services and skip without them (`DPS_TEST_REQUIRE_SERVICES=1` makes a
missing service fail): `docker compose up -d dps-db` (marker `db`), `--profile sources` (`pg_source`,
`s3`, `mongo`), `--profile oracle` (`oracle`), `--profile docs` (`ocr`), `--profile scanner` (`browser`: the
console checks and the scanner's browser tests, which use the demo site at `DPS_TEST_DEMO_SITE`). The milestone gates against the full stack:

```bash
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "docs or ocr"
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "oracle or mongo"
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m browser
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m slow tests/connectors/test_resume_after_kill.py
DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/connectors/test_conformance_*.py -m "pg_source or oracle or mongo"
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "browser or db" tests/scanner tests/test_no_values_in_logs_m4.py   # M4
```

Tests and tools take endpoints only from `DPS_TEST_*` variables (defaults follow the compose port
variables); without Docker, `tools/scratch_pg.sh start all` runs PostgreSQL 16 for the `db` and
`pg_source` tests. Console checks (Playwright 1.55.0, the `uitest` extra) write screenshots to the
git-ignored `reports/console/`. `python tools/make_demo_docs.py` regenerates the committed
synthetic demo documents; `python tools/eval_detectors.py run --corpus tests/eval/corpus …` the
detector evaluation.

`npm run build` writes `frontend/dist`, which the API process serves at `/` when it exists.
`npm test` runs both console suites: `node --test` over `frontend/tests/*.test.ts` (Node >= 22.6) and
Vitest over `frontend/src/**/*.test.ts`. `demo/documents/` holds synthetic KYC documents in every
supported format (text and scanned PDF, PNG, DOCX, TXT, CSV); the console's "Load demo KYC scan"
serves `frontend/public/demo/kyc-scanned.pdf`. Optional extras: `ner` (Presidio + spaCy, higher recall
on names and places), `fpe` (FF1 format-preserving encryption, used only with `DPS_FPE_ENABLED=true`),
`docs` (documents: pypdf, pdfplumber, pypdfium2, reportlab, python-docx, Pillow), `db` (asyncpg:
metadata database and PostgreSQL connector), `connectors` (sqlglot, pyarrow, boto3, Oracle and
MongoDB drivers), `tabular` (the Gaussian copula), `synth-ml` (the optional MOSTLY AI engine),
`scanner` (Playwright 1.55.0 and Pillow for the browser-worker; never in the app image), `uitest` /
`e2e` (Playwright 1.55.0 for the console checks), `vertex` (laptop path to Gemini). Production goes
through the Stellar gateway — see `HANDOFF.md`.

## How it works

detect (structural key names + patterns/checksums + optional NER + client declarations) → plan
(policy → one method per finding, with a reason) → transform (AES-SIV tokens, FF1, keyed date shift,
surrogates, the bank's tokenizer…) → verify (no original value survives in the output) → report
(counts, methods, fallbacks, verification — never a value). Keys and synthesis seeds are returned to
the caller and never stored. Synthetic text goes through the LLM as typed placeholders only, checked
by an egress verifier.

Jobs: connect (read-only sessions, least-privilege checks) → discover (sample, classify each column,
propose a snapshot) → review (accept or override per column) → run (key-space partitions claimed by
workers from a PostgreSQL queue, transformed by the same engine, written in committed batches with a
write ledger and a checkpoint after each, so a killed worker's partition resumes without writing a
row twice) → evidence (a hashed, printable pack of counts, hashes, methods, key ids and, for
synthetic data, measured fidelity and privacy).
