# WP-I (m5): merge of M5 into main

- Checkout `/Users/neelu/document_intelligence/data-privacy-services`, branch `main`. Author NilenduGanguli <nilendu.ganguli@gmail.com> on every commit, no trailers. Explicit pathspecs (except the merge commit itself). No push, no tags.
- Resume check: main was clean at `dcf268f`, `m5` was not merged and no `I-m5.md` existed, so I started from scratch.
- Lane input: `{"lane":"m5","groups":["G2:PASS","G3:PASS","G4:PASS"],"verify":"PASS","open":[]}`. No open lane finding to fix (step 3).
- Other commits landed on main while I worked: `afd5998`, `f7bc7eb` and `81546a6` (gitleaks fingerprints and an auto-push script; not mine). The final gates ran on a tree that included all three.
- Scratch: `~/.cache/dps-build/i-m5/` holds the logs, `env.sh` (contract §12.2 defaults) and `scripts/{synth_check,log_scan}.py`.

## Commits

| Commit | What |
|---|---|
| `8378da7` | `merge: M5 into main` (`--no-ff m5`). 11 files conflicted; both sides kept in each (details below). |
| `845e230` | `fix(integrate): oracle's TLS protocol option is endpoint-defining (SEC-3)` |
| `1b1f7d6` | `fix(integrate): synthesize review joins main's post-write gate; mixed-PII columns never kept by synthesize` |
| `b42d257` | `fix(integrate): Reports shows review required for runs main's gate or D-A flags` |
| `0e4fad7` | `test(integrate): run validation resolves policy references; admin rule answers before TLS` (the code fixes are in the merge commit) |
| `0bd6977` | `fix(integrate): type the Vertex contents list so mypy passes with every extra installed` |
| `85ee587` | `fix(integrate): a synthesize append checks the identifiers it wrote, not the target's own rows` |
| `709f69a` | `docs(integrate): HANDOFF and contract record the M5 merge and the identifier check scope` |

## Conflict resolution (step 1)

- **HANDOFF, contract §5.1.** Kept main's text: SEC-2 tenant-scoped `demo-secrets` under `default/`, and the SEC-2/SEC-3 rules. Added M5's `tokenizer-mock` row and its TLS-only paragraph.
- **`dps/evaluation/corpus.py`.** Kept main's free-text `ACCOUNT_NUMBER` branch, then M5's `label` and `PAN_IN` surrogate.
- **`routers/connections.py` PATCH.** Kept main's `changed_fields` audit counts. Main's production-admin check is inside `update_connection`, under the row lock, so I dropped M5's router copy of it. Kept M5's TLS check, but the admin rule now answers before it: a production connection gets 403 before 422.
- **`routers/jobs.py`.** Kept both imports. `POST /jobs/run/validate` now resolves `@name[:vN]` the same way `POST /jobs/run` does. Without this, wizard step 5 would refuse every tenant policy reference.
- **`jobs_run.py` (6 hunks).**
  - Both import sets.
  - Main's `scan_written` and `verification_outcome`, next to M5's `copied_personal_columns`, `synthetic_kept_columns`, `kept_review`, `_sampled_columns`, `_written_identifiers` and `_identifier_check`.
  - `_post_sample` runs main's `scan_written`, plus M5's `synthetic_kept` review for synthesize runs. `snapshot` stays a required parameter (main).
  - `_store_evidence` uses main's `passed`/`status`/`review_required` and then adds the D-A review (step 2).
- **`targets.py` docstring.** Merged both lists of error codes.
- **`types.ts`.** Kept main's `EvidenceSummary` (`review_required`, `error_code`) and M5's whole block of types.
- **`Reports.tsx`.** Took M5's page (EvidenceTable, filters, sweeps, audit). Main's review-required and N3 behaviour was ported into `lib/reports.ts` (step 2). M5 already had N3.
- **Tests.** In `tests/connectors/test_targets.py` I kept both suites; the two `_with` helpers were identical, so one stays. In `tests/test_evidence.py` I kept both suites.
- **Migrations.** 001–010 (M0–M2) and 040–045 (M5). No duplicates; M3's 020–029 and M4's 030–039 are still free.
- **Compose.** Every host port and the image tag are defaulted variables. Profiles: `docs`/`full` (ocr mock), `sources`/`full` (source-pg, minio, minio-init, demo-secrets, seed-sources), `tokenizer`/`full` (tokenizer-mock). `app`, `worker`, `dps-db` and `migrate` have no profile and always run, as they did on main before.
- **Console.** Nav and routes cover every merged page: Overview, Playground (+ documents), Policies (+ `:name`), Data sources & jobs (list, wizard, detail), UI scanner placeholder (M4), Reports & audit (+ evidence pack), Admin.

## Adaptations to main's newer behaviour (step 2, each test-first)

1. **SEC-2 (tenant-scoped secret refs).**
   - M5 adds no demo secret files and no seed script that writes secret refs. The tokenizer token is an env var or a `_FILE` setting, not a `secret_ref`.
   - Every M5 path that reads credentials goes through main's `load_credentials` → `resolve_secret_ref(tenant, ref)`: `registry.open_connector`/`open_target_store`, which includes the TLS check that reads the Oracle `dsn` secret.
   - The demo refs in `tools/smoke_test.py` and `m2_api.py` are tenant-relative and land under `default/`.
   - The whole suite, including the M5 integration tests that register refs for `t1`, is green.
   - Nothing needed to change.
2. **SEC-3 (endpoint-defining params).** M5 added the `options.protocol` connection option (Oracle tcp/tcps). Main's classification test failed on it as designed. It is now in `ENDPOINT_PARAMS["oracle"]`: tcp↔tcps decides whether credentials travel in clear text. Test: `tests/test_connection_endpoints.py`.
3. **Discovery fix (mixed-PII never auto-kept; post-write kept-column hits → review required) for synthesize runs.**
   - In `_post_sample`, keyed pseudonyms count as written by DPS (planned). Tabular-sampled columns are synthetic, so they are excluded from "unclassified". Without this, every copula or pseudonymised run would have needed review.
   - New pure helper `apply_synthetic_review`:
     - D-A review columns make the verification `review_required` and not passed;
     - it never downgrades `failed`/`incomplete`;
     - an empty D-A list never clears a review that main's post-write gate already required. M5's original code would have reset `review_required` to false there.
   - Tests:
     - `tests/test_post_sample_review.py`: 4 new tests;
     - `tests/connectors/test_demo_pii_survival.py`: synthetic copies of contacts, cards, kyc_documents and addresses. Mixed `contacts.value` is `MIXED_PII` and planned. Per-column survivors are 0, except one chance name-pool overlap on `cards.cardholder_name` (bounded at 5 %, M5 N1);
     - a synthesize run that keeps a personal column → `status: review_required`, the column named by both checks, and the evidence list flag set.
4. **Reports.**
   - `GET /reports/evidence` summaries carry `review_required` and `verification_status` (`tests/test_reports_review_summary.py`).
   - The console's `verificationChip` shows amber "review required". A hit or an unfinished run outranks the review. Covered by 2 new Vitest cases.
   - `fromSummary` (M2 fallback) carries the flag.
5. **New finding fixed: synthesize append identifier check (`85ee587`).**
   - **Symptom.** `tools/demo_m2.py` on the merged stack failed its append step (`bank_perf.transactions` REVIEW_REQUIRED, `counterparty_iban` in_source 1).
   - **Source-vs-target counts.** All 53 matching rows had keys ≤ 900000001000. These are the 1,000 seed rows that `bank_perf` already held, and they share the demo source's single IBAN. The 9,685 appended rows had 0 matches. This was a false review, not a leak.
   - **Fix.**
     - At prepare, an append synthesize into another table records the target's largest single-integer key (`identifier_floor`).
     - At finalize, the check reads only the rows after that key (`scope: written_rows`).
     - Fail-closed: if fewer rows follow the floor than the run wrote, it re-checks every row (`scope: whole_table`, `scope_reason: rows_below_floor`). A non-integer or composite key also means a whole-table check, as before.
   - **Tests.**
     - 3 unit tests in `tests/test_identifier_check.py`, including the fallback;
     - an integration test that appends into a table holding 40 copies of source rows. Appended national_id survivors are 0, own rows still match, `scope` is `written_rows`, and the pack holds no values.
   - **After the fix.** `demo_m2` passes all 13 jobs. The append pack reads `scope: written_rows`, 9,685 checked, in_source 0. A SQL check over keys > the pre-run max finds 0 source IBANs.
6. **mypy with every extra installed.** `vertex` (google-genai 2.30.1) types `generate_content`. `contents` is now `list[types.PartUnionDict]`. This was latent on main, which never ran mypy with that extra installed.

## Gates on the merged tree (final, after `709f69a`; HEAD includes the concurrent commits)

Setup:
- Fresh install: `uv pip install -e ".[ner,fpe,vertex,docs,db,connectors,uitest,e2e,tabular,dev]"`. Every extra except `synth-ml` (mostlyai + torch, "never in an image"; its 2 tests skip).
- Stack:
  - first `docker compose --env-file docker/compose.sources.env --profile full down -v`;
  - then `DPS_TOKENIZER_MOCK_PORT=8430 docker compose --env-file docker/compose.sources.env --profile full up --build -d`;
  - rebuilt after the last code fix.
- Default ports 8400/5441/5442/9010-9011/5016. Tokenizer-mock is on 8430 because 8420 is held by `dps-wt-m4-demo-site-1`; I did not touch that lane.
- `/readyz` ready: db (migrations verified), jobs, tokenizer http up, ocr up, fpe ff1.

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` | 311 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success, 128 files |
| `pytest -q -rs` (stack up, after smoke) | **2069 passed, 6 skipped**. Skips: tesseract binary ×2 (host), mostlyai ×2, spaCy model ×2 |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "docs or ocr"` (M1) | 139 passed, 2 skipped (tesseract binary on host) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "db or pg_source or s3"` (M2 + M5 db/pg_source suites) | **294 passed**, 0 skipped |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m browser` | 3 passed |
| M5 DoD 13 files (`test_llm_egress`, `test_mostly_linked`, `test_posture_m5`) | 48 passed, 1 skipped (mostlyai) |
| `frontend: npm ci && npm test && npm run typecheck && npm run build` | node 42/42, Vitest 201/201 (14 files), tsc clean, build ok (only Vite's >500 kB chunk advisory) |
| `DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py` | **all 34 checks passed** against :8400 (run on the fresh stack and again on the rebuilt one) |
| `python tools/demo_m2.py --group im5-uat-refresh-2` | 13 steps succeeded, all verifications passed, referential check ok, 0 orphans |

## Privacy proofs (counts only; `synth_check.json`, `log_scan.py`)

**Live L2 synthesize** through the HTTP API (`bank-pg-ro` → `bank-uat`, table, 20,000 rows):

| Case | Verification | Fidelity | Exact rows (pack / SQL on non-key columns) | Identifier check |
|---|---|---|---|---|
| copula | passed | 0.953 | 0 / 0 | national_id 0/20,000 in source; tax_id 0/12,069 |
| surrogate (`tabular: none`) | passed | – | – / 0 | same as copula |

Per-column target values found anywhere in the same source column:

| Column | copula | surrogate |
|---|---|---|
| `customer_id`, `national_id`, `tax_id`, `mobile`, `notes`, `relationship_manager_id` | 0 | 0 |
| `email` | 1 (reserved-domain overlap, M5 N8) | 0 |
| `full_name` | 884 (name-pool overlap on unrelated rows, M5 N1) | 901 (same) |

**Masked target** (`customers_smoke`, obfuscate):
- `full_name`, `national_id`, `tax_id`, `email`, `mobile` and `notes`: 0 in the source.
- `customer_id`: 3. FPE landed on another real id (M5 N9, known).
- `relationship_manager_id`: 20,000 (kept, see open issue 3).

**Log scan.** 127,127 distinct sensitive demo values (≥ 6 characters), from customers, accounts, contacts, cards, kyc_documents and addresses, were searched in the logs of all 11 containers. 0 hits.

**LLM counter.** `dps_llm_raw_pii_sent_total` is 0.

## Stack left running for the verifier

Project `data-privacy-services`, profile `full`, image `data-privacy-services:local` built from HEAD. All services are healthy; the one-shots exited 0. Start it the same way, with `DPS_TOKENIZER_MOCK_PORT=8430` while the m4 lane runs. Demo connections and jobs exist (smoke + `demo_m2` + `synth_check`). The tree is clean.

## Open issues / notes for the verifier

1. **Fixed here, needs independent review:** the identifier check scope (`85ee587`). It is a change to a verification check. It is fail-closed by design (it falls back to every row), but deserves a reviewer's eye in WP-F1.
2. **`tokenizer-mock` default host port 8420 collides with the M4 lane's demo-site port.** It only matters while lanes run in parallel, so I left the default alone. Documented in HANDOFF §12.
3. **Pre-existing on main, not introduced by this merge:** an obfuscate copy keeps `customers.relationship_manager_id` (an employee id, unclassified integer FK) unchanged. All 20,000 values are in the source. Synthesize pseudonymises it (D-A). WP-F1 should decide whether employee ids are personal data in the obfuscate path.
4. **Demo seed quirk:** `generate_rows(seed=5)` cards have 1 distinct PAN, and `bank.transactions` has 1 distinct IBAN. This makes per-value checks on those columns weak in the demo. Polish item for F3/F4.
5. **`tools/demo_m2.py` keeps the group key in `var/demo/<group>.json`.** After `down -v` the saved group is gone and the demo fails with `group_not_found` unless a new `--group` is passed (known A-N1, WP-F4).
6. **Not run here, deferred per plan:** `synth-ml`/`ml` tests (MOSTLY SDK), `-m deploy`/kustomize (T24/T25), `tests/security` (T17), the M5 console browser walk on main (M5 lane r3 walked the 5 pages; the M2 console tests passed here), and the M3/M4 markers (not merged yet). Tags: none (no `v*` tag for M5 per plan; tagging is WP-F6).
