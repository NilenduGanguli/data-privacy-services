# M5 lane verification, round 1 (independent)

- Worktree `/Users/neelu/document_intelligence/dps-wt-m5`, branch `m5`, HEAD `5694280` (fix(m5): wire the console to the API as built). The tree was clean at the start and no earlier M5-verify report existed, so this is a fresh run.
- I changed no code and made no commits. Screenshots are saved **untracked** in `docs/screenshots/m5/`: 21 PNGs, 14 MB. I did not commit them because the verdict is FAIL.
- The stack was stopped with `docker compose ... down`. Volumes are kept so the failure can be reproduced; `down -v` was not run because the verdict is not PASS.
- Scratch files are in `~/.cache/dps-build/m5verify/`:
  - scripts: `l2_synth.py`, `trace_worker.py`, `scan.py`, `walk/*.py`;
  - logs and results: `*.log`, `*.json`, `walk/shots`, `walk/mocks`.

  The value dumps used for scanning (`all-logs.txt`, `audit.txt`, `evidence.txt`) are mode 0600.

## Verdict: FAIL

There is one functional blocker and one D-A acceptance failure. Everything else is green.

## Gates

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 301 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success, 126 source files |
| `./lane.sh pytest -q -rs`, stack up | **1908 passed, 6 skipped**, 245 s. Skips: tesseract binary ×2, `mostlyai` ×2 (the `ml` extra), presidio/`ner` ×2 |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "docs or ocr"` | 138 passed, 2 skipped (tesseract binary on the host; the image has one) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "db or pg_source or s3"` | **266 passed** |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m browser` | 3 passed. It overwrote no tracked file |
| `cd frontend && npm ci && npm test && npm run typecheck && npm run build` | ci ok; node --test 42/42; Vitest 190/190 (14 files); `tsc` clean; build ok (only Vite's >500 kB chunk advisory) |
| Stack | See below |
| `DPS_SMOKE_REQUIRE_OCR=1 ./lane.sh python tools/smoke_test.py` | **all 34 checks passed** against `:8415`. The M5 smoke block belongs to T24, which is deferred |

**Stack.** I ran it from clean volumes (`down -v`, then up) with:

```
DPS_TOKENIZER_MOCK_PORT=8426 docker compose --env-file .env --env-file docker/compose.sources.env --profile full up --build -d
```

- All services were healthy, and the four one-shots exited 0.
- I polled `/readyz` (never `--wait`): `ready: true`. These components were ok: api-core, llm-gateway (stub, `coin_tls` verified), fpe ff1, tokenizer (http), ocr (azure-read-v3.2), db (migrations verified), jobs, keys (db+static).
- `ner` reported patterns-only, as expected.
- `browser` is M4 and not in this lane.

Not run because the work is deferred to the finish stage (T0, T8B, T17, T24–T28): `-m deploy`, `tests/security`, `flow_report.py`, `demo_seed.py`/`e2e_demo.py`, `settings_table.py`, the release tag, and the `oracle`/`mongo` markers (M3).

## L2 synthesize on the demo Postgres source (HTTP API, `bank-pg-ro` → `bank-uat`, table target)

| Case | Result |
|---|---|
| copula `bank.customers` | **dead, `internal_error`**. This is reproducible: I ran it twice, and the merge-ui report already listed it as open issue 1 |
| copula `bank.accounts` | succeeded, 30,000 rows. Pack: fidelity 0.989, **exact_row_matches 0**, NNDR 2.71 (pass), MIA proxy run (AUC 0.50), utility block present ("not run", no `utility_label`), verification passed |
| surrogate synthesize `bank.customers` (`tabular: none`) | succeeded, 20,000 rows. Verification passed. A surrogate pack carries no privacy block, by design |

A first attempt of the surrogate case was refused with 409 `snapshot_not_accepted`. That was my own concurrency: the pytest run re-discovered `bank.customers` and superseded my snapshot. The rerun succeeded.

### Root cause of the copula `bank.customers` failure (blocker)

I stopped the lane worker and ran a one-off worker. It was the stock worker, plus a patch that prints only the exception type and stack frames, with no message and no locals. The run gave this stack:

```
jobs_run.py:371 prepare -> :536 _prepare -> :706 _prepare_tabular -> tabular.py:574 prepare_tabular
-> jobs_run.py:699 make -> :271 _transformer -> engine/batch.py:413 BatchTransformer.__init__
ValueError("a pseudonym column is also planned or sampled")
```

The demo `customers.relationship_manager_id` is a declared FK to `employees`. Discovery reports it as `entity_type None`, int, distinct ratio 0.246. The column then lands in two places:

- `targets.pseudonym_columns(..., shape=live shape)` selects it for a D-A B2 keyed pseudonym;
- `tabular.tabular_columns` also models it with the copula, because it skips only keys, text columns and classified columns, and never declared FKs.

`BatchTransformer` therefore refuses the overlap. `jobs_run.prepare` treats the `ValueError` as unexpected and retries it as `internal_error` until the job is dead.

The tests miss this because the integration fixture `source_pg.customers_table` has no FK, so `tests/connectors/test_synthesize_copula.py` passes.

Suggested fix:

- `tabular_columns` should exclude the pseudonym, declared-FK and identifier columns that `pseudonym_columns` will take. It could take them as an argument, or apply the same `_identifiers` rule.
- Add an integration test on a table with a declared FK, ideally the seeded `bank.customers`.
- Optionally, `prepare` should map this class of plan conflict to a value-free `PrepareFailure` and not retry it.

### Source-vs-target per-column comparison (counts only)

Each check counts target values that occur anywhere in the same source column, plus "same value at the same key".

**copula `bank.accounts` → `v_acct_copula_215757`** (30,000/30,000 rows):

- Exact non-key row matches: **0**.
- `customer_id`: 0 (FK pseudonymised). `account_number`: 0. `iban`: 0.
- `balance`: 11 coincidental equal amounts, with 0 at the same key.
- **`account_id`: 1 target key equals a real source key.** The colliding row shares no other column with that source row (checked: customer_id, account_number and balance all differ).
- `ifsc`: kept (one distinct value in the source). `product`, `currency`, `status`, `opened_on`: modelled categorical or date values from the same domain.

**surrogate `bank.customers` → `v_cust_surr_220608`** (20,000/20,000 rows):

- Exact non-key row matches: **0**.
- 0 survivors: `national_id`, `tax_id`, `email`, `mobile`, `notes`, `relationship_manager_id` (FK pseudonym).
- `full_name`: 849 target names exist somewhere in the source, with 0 at the same key. The demo source was itself generated from the surrogate name pool, so this is pool overlap, not copying.
- `date_of_birth`: 13,942 values in the source, with 0 at the same key. That is the size of the date domain.
- **`customer_id`: 1 target key equals a real source key**, the same collision effect as `account_id`.
- `risk_score`, `segment`, `kyc_status`, `country_code`, `id_type`, `created_at`, `updated_at`: kept verbatim. Distinct counts are identical to the source (for example `created_at` has 19,925 distinct values in both).

### D-A key collision (acceptance failure: "no real key in synthetic targets")

The integer pseudonym (`KeyPseudonyms.integer` → `_cycle`) is a keyed cycle that has no fixed point. However, for values below 10⁹ it maps into the whole `[0, 10⁹)` domain, which contains the source's own key range (all source ids are 9 digits; target ids have 5–9 digits).

Another row's real key is therefore reproduced with probability of about n²/10⁹:

- observed: 1 per run at 20,000 and at 30,000 rows;
- at 10 million rows that is roughly 100,000 rows.

This is a collision, not a link, but the §6 proof ("no real key in synthetic targets (D-A)") fails literally.

Suggested fix: pseudonyms in a range disjoint from the source key space, or cycle-walking past values present in the source key set within the job group (deterministic per group). Add an assertion of 0 key overlap at realistic n.

## Browser walk (private Playwright 1.55.0 at `~/.cache/dps-build/pw-m5`, production bundle served by the app on `:8415`)

**Sweep.**
- Pages: `/`, `/reports`, two evidence packs (copula and surrogate), `/admin`, `/policies/dpdp-pseudonymise`, `/data-sources`, `/playground`.
- Each was loaded at 1440 px and 390 px, light and dark: **32 loads, 0 console errors, 0 API responses ≥ 400, 0 px horizontal overflow** (390 px included).
- The API key never appeared in page text.

**Overview vs mockup 01.**
- Same sections and order: posture header with the policy pack (`gdpr-pseudonymise v1`), 4 KPIs (Raw PII sent to LLM **0**), trend, posture score, three layer cards, findings by entity type, and recent jobs.
- The dead copula job shows as `dead`.

**Reports & audit (mockup 09).**
- KPIs come from the data.
- CSV export `dps-audit-…csv` (14 KB).
- Roll-up downloads: JSON 609 B, PDF 2,475 B starting `%PDF-`.
- Pack JSON and PDF downloads work.
- Filters are kept in the URL (`?layer=conn&outcome=ok`). The OK filter returns `ok`/`succeeded`/`dry run`; the Failed filter returns 1 pack.
- Scope "all tenants" works, and "Load 50 older" goes from 50 to 97 rows.

**Evidence pack (mockup 10).**
- All 8 sections render, including **6 Synthetic fidelity and privacy**: exact-row matches "0 of 30,000 enforced", NNDR, QI uniqueness, MIA proxy, and utility.
- The JSON and PDF downloads work.
- The API PDF is `application/pdf`. Its text (via `pdftotext`) has the synthetic section, "Exact-row matches 0" and the "measured, not claimed anonymous" label, and contains no source value or key.

**Admin (mockup 11).**
- Create key: the raw key has the correct shape. After "I have stored the key" it is absent from the DOM, `localStorage`, `sessionStorage` and the URL.
- Rotate with 24 h grace leaves 2 rows (Rotate/Disable, then Disable).
- Rotate with grace "now" marks the old key "rotated, disabled".
- Disable, then re-enable, works.
- A one-letter name is refused inline.
- Adding a tenant and editing its quota work.
- A host is added and normalised by the server, an invalid host is refused inline, and the host is removed.
- The Registered connections card lists 4 connections with "who may write".
- Posture checks: 11 rows, 5 pass in `local`, as expected (bootstrap key, stub LLM and no KEK file fail by design locally).
- Service health renders.
- 0 console errors, 0 failed responses.

**Policies (mockup 05).**
- Shown: presets list, treatment matrix with 34 of 58 entity types, declared fields, and the recognizer builder.
- Adopt a preset → v1.
- Live test: "1 match · 1 on the allow list".
- The recognizer and a declared field go into the draft ("Draft v2 · 16 changes").
- Plan preview: 12 findings.
- Publish v2 with a ticket; the v1→v2 diff shows 2 additions.
- `dpdp-pseudonymise-v2.yaml` downloads and contains the recognizer.
- 0 console errors, 0 failed responses.

**Mockup comparison** (renders in `m5verify/walk/mocks`). At 1440 px each page has the same sections, order and labels as its mockup. The differences are data volume, the theme and the self-review deviations already listed (no 30-day health strips, no weekly export schedule).

## Privacy proofs (programme §6)

**Log scan.** I scanned every container log of project `dps-wt-m5` (all 11 services, 140 KB). The values were all distinct ≥ 8-character values of 15 sensitive demo columns, about 190K values: customers full_name/national_id/tax_id/email/mobile/notes, accounts account_number/iban, contacts.value, employees name/email/phone, cards pan/cardholder_name, addresses.line1. I also searched for the 5 raw API keys created in the Admin walk, the bootstrap literal, the tokenizer token and key literals, and key-shaped `dps_…` tokens.

Result: **0 hits**. A positive control (3 real rows) was detected 3/3.

**Audit rows.** I scanned `dps.dps_audit_log` (99 rows) with the same scan: 0 values, 0 keys. The audit rows are value-free.

**Evidence rows.** I scanned `dps.dps_evidence` (7 rows) and the evidence PDF text: 0 values, 0 keys.

**LLM counter.** `dps_llm_raw_pii_sent_total` is 0 on the app and on the worker (`:9400`). No `by_tenant` series is emitted, which is correct while the count is zero.

**Counting server (D4).** No counting server applies to M5. It concerns the M4 scanner.

## Blocking

1. **Copula synthesize of the demo `bank.customers` dies.** `dps/layers/connectors/tabular.py` `tabular_columns` models the declared-FK column `relationship_manager_id`, which `pseudonym_columns` also pseudonymises. `BatchTransformer` then raises `ValueError` and the job is retried to `dead`/`internal_error`. The fix and test are described above.
2. **D-A "no real key in synthetic targets" fails.** `dps/engine/synth/keys.py` `KeyPseudonyms.integer`'s pseudonym domain overlaps the real key space. I observed 1 real `customer_id` and 1 real `account_id` in the targets (collisions on unrelated rows). The expected count grows as n²/10⁹.

## Non-blocking / owner decisions

- **D-A kept attributes.** Surrogate (non-copula) synthesize keeps non-personal columns verbatim. That includes near-unique timestamps (`created_at`/`updated_at`, 19.9K distinct values out of 20K). Each synthetic row can be joined back 1:1 to its source row through them, together with that row's real `risk_score`, `kyc_status` and `segment`. D-A allows this ("kept only when classified non-personal"), but it weakens the "no path back to the original" rationale. Recommend generalising, shifting or modelling high-cardinality kept columns.
- **Pack / evidence consistency.** The copula accounts pack shows the post-write sample chip "review" (522 findings in kept columns · 220 in transformed columns), while the header reads "verified · 0 leaks". The IFSC kept-column question was raised in the merge report and is still open.
- **Lane ports.** The lane `.env`/`.env.lane` has no `DPS_TOKENIZER_MOCK_PORT`. The compose default 8420 collides with M4's `DPS_DEMO_SITE_PORT` 8420, so I set 8426 for this run. Add it to the lane env (8426 is free in the §4 table).
- **Compose invocation.** HANDOFF §3 uses `--env-file docker/compose.sources.env` alone, which drops the lane `.env` (ports and image tag). Lanes must pass `--env-file .env` as well, which the comment inside `compose.sources.env` already says.
