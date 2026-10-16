# M5 lane verification, round 3 (independent)

- Worktree `/Users/neelu/document_intelligence/dps-wt-m5`, branch `m5`. Verified HEAD `0d43420` (fix-r2: never-issued government-ID surrogates + post-write identifier check).
- Start state: tree clean except r2's untracked `docs/screenshots/m5/`. No earlier r3 report existed: fresh run.
- No code changed. One commit: **`1be3a56` `docs: M5 verification screenshots`** (21 PNGs regenerated in this round, explicit pathspec `docs/screenshots/m5`, author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailers).
- Stack stopped with `docker compose ... --profile full down -v` (0 containers, volumes removed). Tree clean.
- Scratch: `~/.cache/dps-build/m5verify-r3/` (scripts adapted from r2, logs, `l2_run{1,2,3}.json`, `mask_cmp.log`, `scan.log`, `ev.pdf`/`ev.txt`, `walk/*.json`, `walk/shots`, `walk/mocks`, `walk/ev-print.pdf`).
  - Change from r2's method: raw API keys and one-time synthesis seeds are **never written to disk**. They are counted in memory against every container log and the audit/evidence rows (`secret_scan.py`).

## Verdict: PASS

The r2 blocker (real national IDs in synthetic targets) is fixed. Over 9 synthesize runs, 0 target `national_id`/`tax_id` values are in the source, raw or normalised. Every gate, integration suite, console page and privacy proof is green. Non-blocking notes are at the end.

## Gates (DoD items in scope; T0, T8B, T17, T24–T28 deferred)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 305 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 128 source files |
| `./lane.sh pytest -q -rs` (`full` stack up, after the smoke run) | **1932 passed, 6 skipped**, 282 s. Skips: tesseract binary ×2 (host; the image has one), `mostlyai` ×2 (`synth-ml` extra: torch-sized, never in an image), presidio ×1 and spaCy model ×1 (`ner` extra plus a model download) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "docs or ocr"` | 138 passed, 2 skipped (tesseract binary on the host) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "db or pg_source or s3"` | **271 passed**, 0 skipped |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m browser` | 3 passed. No tracked file changed |
| `tests/test_llm_egress.py tests/test_mostly_linked.py` (DoD 13) | 22 passed, 1 skipped (`mostlyai`). Posture refusing `verify_warn_allowed` and the retention scheduling tests ran in the full suite (green) |
| `cd frontend && npm ci && npm test && npm run typecheck && npm run build` | ci ok; node `--test` ok; Vitest **190/190** (14 files); `tsc` clean; build ok (only Vite's >500 kB chunk advisory) |
| `DPS_SMOKE_REQUIRE_OCR=1 ./lane.sh python tools/smoke_test.py` | **all 34 checks passed** against `http://127.0.0.1:8415`, 0 SKIP. The M5 smoke block is T24 (deferred) |
| Not run (deferred or other lane) | `-m "oracle or mongo"` (M3), `-m deploy` and kustomize (T24/T25), `tests/security` (T17; directory does not exist yet), `tests/test_no_values_in_logs_m5.py` and `test_llm_label_l2_l3.py` (T17/T8B; files do not exist yet), `flow_report.py`, `demo_seed.py`/`e2e_demo.py`, `settings_table.py`, release tag |

## Stack

- Command, from clean volumes (`down -v` first): `DPS_TOKENIZER_MOCK_PORT=8426 docker compose --env-file .env --env-file docker/compose.sources.env --profile full up --build -d`. The default 8420 is held by another lane's container (N7).
- Every service was healthy. The one-shots (`demo-secrets`, `migrate`, `minio-init`, `seed-sources`) exited 0.
- I polled `/readyz` (never `--wait`): `ready: true`. Components ok: api-core, llm-gateway (stub, `coin_tls` verified), fpe ff1, tokenizer (http), ocr (azure-read-v3.2), db (migrations verified), jobs, keys (db+static).
- `ner` reports patterns-only (expected; extra not in the image). `browser` belongs to M4.

## L2 synthesize on the demo Postgres source (HTTP API, `bank-pg-ro` → `bank-uat`, `table` target)

**Setup.** 3 runs × 3 cases (copula customers, surrogate customers, copula accounts). All 9 jobs succeeded: 20,000 / 20,000 / 30,000 rows.

**Per-run summary:**

| Case | Exact rows (pack / SQL on non-key columns) | Real keys in target | Identifier check (checked / in_source / issuable) | Pack |
|---|---|---|---|---|
| copula customers | 0 / 0 (×3) | `customer_id` 0; `relationship_manager_id`→`employees` 0 | `national_id` 20,000/0/0; `tax_id` 12,069/0/0 | fidelity 0.975 / 0.976 / 0.957; NNDR 1.44 pass; MIA run; utility block present ("no utility_label"); verified, review [] |
| surrogate customers | – / 0 (×3) | 0 / 0 | same | verified, review [] |
| copula accounts | 0 / 0 (×3) | `account_id` 0; `customer_id` 0 | `account_number` 30,000/0/0; `iban` 2,916/0/0 | fidelity 0.972 / 0.987 / 0.989; NNDR 3.66 pass; MIA run; verified |

**Per-column comparison.** Each count is target values found anywhere in the same source column. I counted both raw text and a normalised form (upper-cased, non-alphanumerics removed). `same_value_same_key` was 0 for every column in every run.

| Column | copula cust (r1/r2/r3) | surrogate cust | Note |
|---|---|---|---|
| `customer_id`, `relationship_manager_id`, `national_id`, `tax_id`, `mobile`, `notes` | 0 | 0 | raw and normalised |
| `email` | 0 / **1** / 0 | 0 | the one hit is on an RFC 2606 reserved domain; every source email is on one (note N8) |
| `full_name` | 870 / 838 / 818 | 836 / 894 / 863 | name-pool overlap on unrelated rows (N1, unchanged) |
| `created_at`, `updated_at` | 0 | 20,000 (kept verbatim) | N2, unchanged |
| `date_of_birth`, categorical columns, `risk_score` | chance / categorical | | expected |
| accounts: `account_id`, `customer_id`, `account_number`, `iban` | 0 | – | |
| accounts: `balance` | 24 / 20 / 28 of 30,000 | – | chance on 2-decimal amounts |
| accounts: `ifsc`, `opened_on`, categorical | kept / chance | – | ifsc has 1 distinct value in the seed (N5) |

**Cross-column check.** I searched every target column for 134,089 sensitive demo values of 6 characters or more, taken from customers, accounts, contacts, employees, cards and addresses. Hits: only `full_name` (N1) and the one reserved-domain `email` above.

**Masked targets (obfuscate) also compared** (`mask_cmp.log`). These are `bank_uat.customers_console` (12,069 rows) and `customers_smoke` (20,000 rows).
- `full_name`, `national_id`, `tax_id`, `email`, `mobile` and `notes`: **0** in the source.
- `customer_id`: 1 and 9 target values equal some source id. On the matching rows `created_at` differs, so these are unrelated customers, not fixed points. This is format-preserving pseudonymisation landing on another real id (M2 masking, N9).
- The in-place tests restored their tables: `bank` vs `bank_restore` differ by 0 rows on all 10 tables.

## Browser walk (private Playwright 1.55.0, `~/.cache/dps-build/pw-m5`, `http://127.0.0.1:8415`)

**Sweep.** 5 pages × {1440, 390} × {light, dark}, against the real API: **0 console errors, 0 failed API responses, 0 px horizontal overflow on all 20**. The bootstrap key never appears in page text.

**Overview (mockup 01).** All parts are present:
- 4 KPIs, including "Raw PII sent to LLM 0";
- protected values per day;
- posture 100 with 4 bars;
- 3 layer cards;
- findings by entity type;
- recent jobs and scans.

**Reports & audit (mockup 09).**
- KPIs: 18 packs, sweeps all passed, 157+ audit rows, "Customer values stored 0".
- CSV export: `dps-audit-…csv`.
- Roll-up downloads: JSON, and a PDF starting `%PDF-`.
- Pack downloads: JSON and PDF.
- Filters live in the URL (`/reports?layer=conn&outcome=ok`). Connectors+OK shows 15 packs and outcomes {ok, succeeded}. Connectors+Failed shows 3 packs, worded "not verified · job failed".
- Tenant scope "All tenants I can read" works.
- "Load 50 older rows": 50 → 100.
- Ticket search: 1 pack.

**Evidence pack (mockup 10), on a copula customers pack.**
- 8 sections, including **6 "Synthetic fidelity and privacy"**: fidelity table, correlation delta, exact rows 0 of 20,000 (enforced), NNDR, QI uniqueness, MIA proxy, utility "not run".
- JSON and PDF download from the page.
- The API PDF is `application/pdf`. Its text holds the synthetic section and the label "synthetic - measured, not claimed anonymous".
- Print preview (print media, as a PDF): 7 pages, sidebar hidden, 0 console errors.

**Admin (mockup 11).**
- Keys:
  - Create shows a raw key of the correct shape **once**. After "I have stored the key" it is absent from the DOM, `localStorage`, `sessionStorage` and the URL.
  - Rotate with the 24 h grace default leaves 2 rows ("Rotate Disable" / "Disable").
  - Rotate with grace "now" marks the old key "rotated, disabled".
  - Disable, then re-enable, works.
  - A one-letter name is refused inline.
- Tenants: added; quota edited to 2M ("0 of 2M").
- Hosts:
  - normalised to `https://payments-uat.bank.internal`;
  - an invalid host is refused inline;
  - Remove works.
- "Registered connections" shows "who may write".
- Posture: "5 of 11 checks pass". The fails are by design in `local`.
- Service health renders.
- In-memory scan of the 5 raw keys: 0 hits in 198 KB of container logs, 0 in audit/evidence rows.

**Policies (mockup 05).**
- Shown: presets, treatment matrix, declared fields, recognizer builder.
- Adopt `dpdp-pseudonymise` → v1.
- Live test: "1 match · 1 on the allow list".
- The recognizer and a declared field go into the draft ("Draft v2 · 16 changes").
- Plan preview: 12 findings.
- Publish v2 with a ticket; the v1 → v2 diff shows 2 additions.
- `dpdp-pseudonymise-v2.yaml` downloads and contains the recognizer.
- The matrix now shows the never-issued synthesize hints (NINO, CURP, NHS and the others).

The Reports, Admin and Policies walks each had 0 console errors and 0 failed responses.

**Mockup comparison** (`walk/mocks` beside `walk/shots`, 1440 px light, plus 390 px dark spot checks). Every page has the same sections, order and labels as its mockup. The differences are data volume, the theme and the deviations documented in `M5-DEVIATIONS-console.md`:
- no 30-day health strips;
- no weekly export;
- UTC days;
- the evidence sheet numbers sections 1–8.

Screenshots: `docs/screenshots/m5/` (21 files, committed in `1be3a56`).

## Privacy proofs (programme §6)

**Log scan** (`scan.py`, in process).
- Scope: all container logs of project `dps-wt-m5`, about 207 KB.
- Searched for:
  - every distinct value of 8 characters or more in 18 sensitive demo columns (about 265K values, including real `customer_id`, `account_id` and `employee_id`);
  - the bootstrap, tokenizer and MinIO literals;
  - key-shaped `dps_…` tokens.
- Result: **0 source values, 0 key-shaped tokens**. The only literal hits were `minioadmin` ×2, both in MinIO's own startup warning.
- The positive control was detected (4 ≥ 3).
- Smoke-test PII literals: 0 in the logs.

**Job seeds and raw API keys.** Six one-time synthesis seeds per run were counted in memory: 0 in the logs, 0 in audit/evidence. The 5 Admin raw keys: 0 / 0.

**Audit rows.** `dps.dps_audit_log` (166 rows): 0 source values, 0 keys. Value-free.

**Evidence rows and PDF.** `dps.dps_evidence` (18 rows): 0 source values, 0 keys.

**LLM counter.** `dps_llm_raw_pii_sent_total` is 0.0 on the app and on the worker (`:9400`). No `by_tenant` series is emitted at zero.

**Counting server (D4).** Not applicable to M5; it concerns the M4 scanner.

## Non-blocking / owner notes

- **N1 (unchanged).** About 820–900 synthetic `full_name` values per customers run equal some real customer's name on unrelated rows: overlap between the name pool and the seed. The owner should confirm names are exempt.
- **N2 (unchanged).** Surrogate (non-copula) synthesize keeps `created_at`/`updated_at` verbatim, which can join 1:1 back to the source row.
- **N3 (unchanged).** Copula decouples `id_type` from `national_id`: a dependent-field consistency gap.
- **N4 (unchanged).** The evidence "Post-write sample: N in kept columns — review" chip (707) sits beside "verified · 0 leaks" while `review_columns` is `[]`. The wording may mislead.
- **N5 (unchanged).** Demo seed: several columns have 1–4 distinct values (`iban`, `ifsc`, `mobile`, `employees.phone`, `cards.pan`). T26 should diversify the seed.
- **N6 (unchanged).** The Overview label shows the most-used policy (`gdpr-pseudonymise v1`), not the adopted `dpdp-pseudonymise` v2.
- **N7 (unchanged).** `DPS_TOKENIZER_MOCK_PORT` is missing from the lane `.env`. The compose default 8420 collides with another lane; I used 8426.
- **N8 (new; as fix-r2 reported).** 1 of 9 runs wrote 1 synthetic `email` equal to a source email. Both are on RFC 2606 reserved domains, because the seeded source is itself made of the engine's reserved-domain emails. A real address cannot collide this way. EMAIL is outside the identifier check by design.
- **N9 (new, M2 scope).** In masked (obfuscate) targets, FPE `customer_id` values land on other real customers' ids (1 and 9 per run; not fixed points). This is inherent to format-preserving pseudonymisation onto the issued space, and it is not a D-A synthesize issue.
- **Walk side effect.** Two aborted Admin-walk attempts (`asyncpg` missing in the Playwright venv; fixed by `uv pip install asyncpg` into `pw-m5`) created extra test keys and tenants (`m5-walk-001555`, `m5-walk-001611`). Their raw keys were never stored or printed, and the volumes are now removed.
- **Skips.** Only `synth-ml` (`mostlyai`), `ner` (presidio plus a spaCy model) and the host tesseract binary, as in r1/r2. DoD 1 literally allows only `ml`, so `ner` and tesseract are carried over as accepted environment limits.
