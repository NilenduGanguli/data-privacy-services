# M5 lane verification, round 2 (independent)

- Worktree `/Users/neelu/document_intelligence/dps-wt-m5`, branch `m5`, HEAD `ad886e8` (fix(m5): never model a pseudonymised foreign key and draw integer pseudonyms from bands no real key reaches).
- Start state: tree clean except the r1 verifier's untracked `docs/screenshots/m5/`. No earlier r2 report existed, so this is a fresh run.
- No code changed and no commits. I regenerated the 21 screenshots and left them **untracked** in `docs/screenshots/m5/` (16 MB); they overwrite r1's. I did not commit them because the verdict is FAIL.
- The stack was stopped with `docker compose ... down`. Volumes are kept so the blocker can be reproduced: the target tables `bank_uat.v_cust_*` are still in source-pg.
- Scratch files are in `~/.cache/dps-build/m5verify-r2/`:
  - L2 scripts and results: `l2_synth.py` (based on r1's script, plus checks for sensitive values anywhere in the target and for real keys in FK columns), `l2_synth.json`, `l2_rerun{1,2}.json`;
  - collision analysis: `nid.py`, `nid2.py`;
  - log scan: `scan.py`, with `all-logs.txt` (mode 0600);
  - evidence PDF: `ev.pdf` / `ev.txt`;
  - browser walk: `walk/*.py` and `walk/*.json`, `walk/shots`, `walk/mocks`, `walk/ev-print.pdf`.

## Verdict: FAIL — one blocker (D-A / §2.4 "no sensitive source value in the target")

Every gate, test suite and console page is green. The failure is in the source-vs-target comparison: synthesize jobs on `bank.customers` write **real national IDs of real customers** into the synthetic target. The number per run is small, but it happens on every run. Details below.

## Gates

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 301 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 126 source files |
| `./lane.sh pytest -q -rs`, `full` stack up | **1912 passed, 9 skipped**, 247 s. Skips: 3 console tests (demo connections missing before the smoke run; see the next row), tesseract binary ×2, `mostlyai` ×2 (`ml` extra), presidio / `ner` ×2 |
| `pytest tests/console` after the smoke run | 3 passed (the 3 skips above now run) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "docs or ocr"` | 138 passed, 2 skipped (tesseract binary on the host; the image has one) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "db or pg_source or s3"` | **269 passed**, 0 skipped (includes the fix-r1 copula FK and demo-customers tests) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m browser` | 3 passed. It overwrote no tracked file (`git status` clean apart from the screenshots) |
| `cd frontend && npm ci && npm test && npm run typecheck && npm run build` | ci ok; node --test 42/42; Vitest **190/190** (14 files); `tsc` clean; build ok (only Vite's >500 kB chunk advisory) |
| `DPS_SMOKE_REQUIRE_OCR=1 ./lane.sh python tools/smoke_test.py` | **all 34 checks passed** against `http://127.0.0.1:8415`, 0 SKIP. The M5 smoke block is T24, deferred |
| `tests/security`, `tests/test_no_values_in_logs_m5.py`, `-m deploy`, `flow_report.py`, `demo_seed.py`/`e2e_demo.py`, `settings_table.py`, release tag, `oracle`/`mongo` markers | Not run: deferred to the finish stage (T0, T8B, T17, T24–T28) or owned by M3. `tests/security` does not exist yet (T17) |

**Stack.** I ran it from clean volumes (`down -v`, then up) with:

```
DPS_TOKENIZER_MOCK_PORT=8426 docker compose --env-file .env --env-file docker/compose.sources.env --profile full up --build -d
```

- Every service was healthy, and the one-shots (`migrate`, `minio-init`, `demo-secrets`, `seed-sources`) exited 0.
- I polled `/readyz` (never `--wait`): `ready: true`. These components were ok: api-core, llm-gateway (stub, `coin_tls` verified), fpe ff1, tokenizer (http), ocr (azure-read-v3.2), db (migrations verified), jobs, keys (db+static).
- `ner` reported patterns-only, as expected.
- `browser` belongs to M4.

## L2 synthesize on the demo Postgres source (HTTP API, `bank-pg-ro` → `bank-uat`, `table` target)

**Run 1** (all 3 cases):

| Case | State | Rows | exact_row_matches (pack / SQL on non-key columns) | Real keys in target | Pack |
|---|---|---|---|---|---|
| copula `bank.customers` | succeeded | 20,000 | 0 / 0 | `customer_id` 0; `relationship_manager_id`→`employees` 0 | fidelity 0.978, NNDR 1.43 pass, MIA run (AUC 0.49), utility block present ("not run", no label), verified |
| surrogate `bank.customers` | succeeded | 20,000 | – / 0 | 0 / 0 | verified |
| copula `bank.accounts` | succeeded | 30,000 | 0 / 0 | `account_id` 0; `customer_id` 0 | fidelity 0.984, NNDR 3.22 pass, MIA run, verified |

**Runs 2 and 3** (both customers cases, each run twice more): every job succeeded, exact rows 0, real keys 0.

**Per-column comparison.** Each cell counts target values found anywhere in the same source column. The cross-column check also covered the sensitive values of customers, accounts, contacts, employees, cards and addresses, 122,043 values of 6 characters or more.

| Column | copula cust | surrogate cust | Note |
|---|---|---|---|
| `email`, `notes`, `tax_id`, `mobile`, `account_number`, `iban` | 0 | 0 | |
| **`national_id`** | **1, 1, 2** | **0, 2, 3** | **BLOCKER (below)** |
| `full_name` | 870 / 867 / 895 | 883 / 849 / 875 | name-pool overlap on unrelated rows (non-blocking, owner note) |
| `created_at` / `updated_at` | 0 (modelled) | 20,000 (kept verbatim) | D-A permits; r1 non-blocking note stands |
| `date_of_birth`, categorical columns, `risk_score`, `balance`, `opened_on`, `ifsc` | chance / categorical | | expected |

### Blocker: real national IDs appear in synthetic targets

**Observed.** Over 6 customers runs (3 copula, 3 surrogate), the synthetic `national_id` held 1, 1, 2, 0, 2 and 3 values that are real source national IDs.
- Every one is a **NINO**.
- Each value is unique in the source, so it identifies one real customer.
- None matched its own source row: the row's name and DOB differ, and the key is pseudonymised.
- In the copula target the colliding values sat on rows whose modelled `id_type` was `AADHAAR` (see N3).

Counts only; the queries are in `nid.py`/`nid2.py`.

**Cause.** `dps/engine/synth/surrogates.py` has dedicated generators that draw from never-issued spaces for SSN (900 area), ITIN, SIN, Aadhaar and cards. `NATIONAL_ID` (this column's inferred entity, a mixed-type column) and `NINO`, `CURP`, `PASSPORT` and similar have **no generator**. They fall to `Reshaper` (`reshape.py`), a keyed derangement of the **issued** value space that keeps the 2-letter NINO prefix (`code_letters`).

A permutation of a densely issued space maps real IDs onto *other* real IDs. The expected count is about n²/|space per prefix|:
- here, with 1,956 NINOs, about 1.5 per run;
- at bank scale (millions of UK customers), most synthetic NINOs would be real people's NINOs.

**Why this blocks.**
- Programme plan §2.4 Verify, and this job's item 3: "no sensitive source value in the target".
- D-A rationale: synthetic output has "no path back"; synthetic tables never carry real identifiers.
- Round 1 classed the same pattern for keys (a chance collision of 1 real `customer_id` on an unrelated row) as blocking, and fix-r1 banded integer keys for that reason.
- Mockup 05 itself gives NINO synthesis as "surrogate · QQ prefix, never allocated" and SSN as "area 900–999, never issued". The built Policies matrix shows NINO only as "surrogate".
- The post-write review (`kept_review`) does not catch this, because the column counts as transformed.
- Fix-r1 open issue 7 recorded one such NINO as "chance". This run shows it is systematic: 5 of 6 runs.

**Fix (suggested).** In synthesize mode, government-ID-family surrogates must come from non-issuable ranges:
- NINO: the `QQ` prefix or the reserved prefixes (BG, GB, NK, KN, TN, NT, ZZ);
- CURP and generic `NATIONAL_ID` of a recognised sub-shape: a reserved range;
- otherwise, fail closed to review.

Also add a post-write target∩source count per identifier-family column. It is a cheap SQL join for `table` targets, and a value above 0 should give `review_required`, or a refusal.

Test: the seeded demo `bank.customers` run asserts 0 target `national_id` in the source, for copula and surrogate.

## Browser walk (private Playwright 1.55.0 at `~/.cache/dps-build/pw-m5`, `http://127.0.0.1:8415`)

**Sweep.** 5 pages × {1440, 390} × {light, dark}: **0 console errors, 0 failed API responses, 0 px horizontal overflow on all 20**. The bootstrap key never appears in page text.

**Overview (mockup 01).** All parts are present:
- 4 KPIs, with "Raw PII sent to LLM 0";
- protected values per day;
- posture score with 4 bars;
- 3 layer cards;
- findings by entity type;
- recent jobs and scans.

The posture is complete. The header chip reads "1 degraded" (ner patterns-only).

**Reports & audit (mockup 09).**
- KPIs, the sweeps chart, failed sweeps, the evidence packs table and the audit log are present.
- Filters live in the URL (`?layer=conn&outcome=ok`): 10 packs and 50 audit rows. Outcome "Failed" gives 3 packs (in-place, job failed).
- The audit scope toggle works, and "Load 50 older rows" goes from 50 to 100 rows.
- Ticket search works.
- Downloads:
  - CSV (18 KB, header `ts,te…`);
  - roll-up JSON and PDF (`%PDF-`);
  - pack JSON and PDF.

**Evidence pack (mockup 10).** Sections 1–8, with section 6 "Synthetic fidelity and privacy":
- fidelity table and correlation delta;
- exact-row matches 0 of 20,000 (enforced);
- NNDR 1.43, QI uniqueness 0.0 %, MIA proxy 0.49;
- utility "not run";
- the "measured, not claimed anonymous" label.

Downloads and print:
- JSON and PDF download from the page.
- The API PDF is `application/pdf`, and its text holds the synthetic section.
- Print preview (print media, as a PDF) is 7 pages; the sidebar is hidden and the synthetic section is present.

**Admin (mockup 11).**
- Keys:
  - Create gives a raw key of the correct shape. After "I have stored the key" it is absent from the DOM, `localStorage`, `sessionStorage` and the URL.
  - Rotate with the 24 h grace default leaves 2 rows ("Rotate Disable" / "Disable").
  - Rotate with grace "now" marks the old key "rotated, disabled".
  - Disable, then re-enable, works.
  - A one-letter name is refused inline.
- Tenants: adding a tenant and editing its quota to 2M works.
- Hosts:
  - an added host is normalised to `https://payments-uat.bank.internal`;
  - an invalid host is refused inline;
  - Remove works.
- Registered connections lists 4 connections with "who may write".
- Posture checks: 5 of 11 pass. The fails are by design in `local`: bootstrap key, TLS, audit strict, verify-warn, stub LLM, no KEK file.
- Service health renders.

**Policies (mockup 05).**
- Shown: presets, treatment matrix (34 of 58), declared fields, recognizer builder.
- Adopt `dpdp-pseudonymise` → v1.
- Live test: "1 match · 1 on the allow list".
- The recognizer and a declared field go into the draft ("Draft v2 · 16 changes").
- Plan preview: 12 findings.
- Publish v2 with a ticket; the v1→v2 diff shows 2 additions.
- `dpdp-pseudonymise-v2.yaml` downloads and contains the recognizer.

The Admin and Policies walks each had 0 console errors and 0 failed responses.

**Mockup comparison** (`walk/mocks`). At 1440 px every page has the same sections, order and labels as its mockup. The differences are:
- data volume and the theme;
- the documented deviations: no 30-day health strips, no weekly export schedule;
- NINO's synthesize note in the matrix (see the blocker).

## Privacy proofs (programme §6)

**Log scan.** I scanned all container logs of project `dps-wt-m5` (11 services, 219 KB). I searched for:
- every distinct value of 8 characters or more in 18 sensitive demo columns (about 263K values, including real `customer_id`, `account_id` and `employee_id`);
- the 5 raw API keys created in the Admin walk;
- the bootstrap, tokenizer and MinIO literals;
- key-shaped `dps_…` tokens.

Result: **0 source values, 0 raw keys, 0 key-shaped tokens**. The only literal hits were 2 `minioadmin`, both in MinIO's own startup warning about its local demo credentials. A positive control (3 planted values) was detected.

**Audit rows.** `dps.dps_audit_log` (136 rows): 0 values, 0 keys. Value-free.

**Evidence rows and PDF.** `dps.dps_evidence` (16 rows): 0 values, 0 keys. The evidence PDF text also has 0 of 130,051 values.

**LLM counter.** `dps_llm_raw_pii_sent_total` is 0.0 on the app and on the worker (`:9400`). No `by_tenant` series is emitted while the count is zero.

**Counting server (D4).** Not applicable to M5; it concerns the M4 scanner.

## Non-blocking / owner notes

- **N1 Name-pool overlap.** About 870 synthetic `full_name` values per customers run equal some real customer's name, on unrelated rows. Faker pools overlap the seed. This is accepted as chance by r1 and fix-r1, but it literally falls under "no sensitive source value in the target". The owner should confirm that names are exempt, or exclude source names.
- **N2 Kept timestamps.** Surrogate (non-copula) synthesize keeps `created_at`/`updated_at` verbatim (19.9K distinct values out of 20K), so each synthetic row joins 1:1 back to its source row. The r1 note stands.
- **N3 Copula decouples `id_type` from `national_id`.** In copula targets, `AADHAAR` rows carry NINO- or CURP-shaped IDs. This is a dependent-field consistency gap (spec §5.6 constraints).
- **N4 Evidence wording.** In copula packs, the "Post-write sample: N in kept columns — review" chip counts sampled and pseudonymised columns as "kept" (707 hits), while the header reads "verified · 0 leaks" and `synthetic_kept.kept_columns` is `[]` (MEDIUM+ only). The r1 note stands.
- **N5 Demo seed oddity.** In the seeded source, AADHAAR (12,069 rows) and SSN (3,913 rows) each have 1 distinct `national_id`, and `iban`, `ifsc`, `pan` and `phone` have 1–4 distinct values. This hides real-distribution behaviour in the demo; T26 should fix the seed.
- **N6 Overview label.** The label reads "policy pack gdpr-pseudonymise v1", the most-used policy by jobs, although the tenant's adopted policy is `dpdp-pseudonymise` v2. This is by design (`mostUsedPolicy`), but it may confuse.
- **N7 Lane env.** `DPS_TOKENIZER_MOCK_PORT` is still missing from the lane `.env`/`.env.lane`; the compose default 8420 collides with M4. I used 8426.
