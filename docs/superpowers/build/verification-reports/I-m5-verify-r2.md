# WP-I (m5): independent verification of main after the M5 merge, round 2

**Verdict: PASS.** No blocking findings.

Round 1 found two blocking issues. Both are fixed by `ea4e751`, and I confirmed each fix independently in a fresh venv that has every extra (synth-ml included) and also in `.venv`, which has no synth-ml:
- **B1:** `mypy` passes with synth-ml installed.
- **B2:** `test_files_synthesize_group_and_tabular_rules` no longer depends on which extras are installed.

Everything else also passed:
- every gate and every integration marker merged so far;
- the frontend and smoke;
- `demo_m2`;
- the SEC-2/SEC-3/SEC-4 and discovery-adaptation probes, including the live TLS-required run;
- the source-vs-target comparisons on every target written;
- the console walk (0 console errors);
- the route-auth walk;
- the log, audit and evidence scans.

## Scope and start state

- **Working copy:** `/Users/neelu/document_intelligence/data-privacy-services`, branch `main`, HEAD `ea4e751`.
  - The tree was clean before and after (`git status --short` empty). I changed no repo file.
  - I made no commits, tags or pushes; HEAD has no tag.
  - The resume check found no earlier `I-m5-verify-r2.md`, no `i-m5-vr2/` scratch and no `venv-I-m5-2`, so this is a fresh run.
- **Scratch:** `~/.cache/dps-build/i-m5-vr2/`
  - `probe/`: scripts. These are r1's probes with the known r1 probe bugs corrected, plus `all_targets_cmp.py`, `key_dom.py`, `attribute.py` and `route_auth.py`.
  - `logs/`: every gate and probe output.
  - `shots/`: 45 screenshots.
  - `override-tls*.yml`: kept outside the repo.
- **Fresh venv:** `~/.cache/dps-build/venv-I-m5-2`.
  - Built with `uv venv -p 3.12` and `uv pip install -e ".[ner,fpe,vertex,docs,db,connectors,uitest,e2e,tabular,synth-ml,dev]"`. That is every extra pyproject defines.
  - mostlyai, torch and pandas import.
- **Stack:** rebuilt from HEAD on clean volumes.
  - `docker compose --env-file docker/compose.sources.env --profile full down -v`
  - then `DPS_TOKENIZER_MOCK_PORT=8430 docker compose … --profile full up --build -d`. Tokenizer-mock is on 8430, as in r1 and I-m5, because 8420 belongs to the m4 lane. All other ports are the defaults.
  - `/readyz` is ready:
    - api-core up;
    - db up, migrations verified;
    - jobs ready;
    - fpe ff1;
    - tokenizer http up;
    - ocr azure-read-v3.2;
    - keys db+static;
    - llm-gateway configured (stub, coin_tls verified);
    - ner patterns-only (expected; there is no spaCy model).
- **Left running:** the stack, in its default configuration. The app was restored after the TLS probe, and a smoke run afterwards passed 34/34. The one-shots exited 0.
- No `dps-wt-*` worktree or container was touched. The contract §12.2 `DPS_TEST_*` defaults were exported (`env.sh`).

## Gates (fresh venv, every extra, main stack up)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` | 311 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | **Success: no issues found in 128 source files** (B1 fixed). Also passes in `.venv` (no synth-ml) |
| `DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py` | all 34 checks passed (fresh stack). 34/34 again after the TLS override was removed |
| `pytest -q -rs` (after smoke) | **2072 passed, 4 skipped, 0 failed** (547 s). Skips: tesseract binary ×2 (host), spaCy English model ×2. The mostlyai tests run |
| B2 test plus the new narrowing test (`-v`) | 2 passed with synth-ml. In `.venv`: `test_mostly.py` and `test_targets.py` give 37 passed, 2 skipped (pandas/mostlyai absent, by design) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "docs or ocr"` | 139 passed, 2 skipped (tesseract binary) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "db or pg_source or s3"` | **294 passed**, 0 skipped |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m browser` | 3 passed; no tracked file changed |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "ml or slow or fpe"` | **18 passed, 0 skipped**. Includes the real MOSTLY AI local engine and resume-after-kill |
| M5 DoD-13 files (`test_llm_egress`, `test_mostly_linked`, `test_posture_m5`), services required | **49 passed, 0 skipped** |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "ner or tesseract or deploy or network or integration"` | 92 passed, 4 skipped (tesseract ×2, spaCy model ×2). No `deploy`-marked test exists yet (T25) |
| `frontend: npm ci && npm test && npm run typecheck && npm run build` | node 42/42; Vitest 201/201 (14 files); tsc 0 errors; build ok |
| `python tools/demo_m2.py --group im5-vr2` | 13 steps succeeded, every verification passed, referential check ok, 0 orphans on 11 FKs |
| `git ls-files … \| grep AKIA…\|PRIVATE KEY` | Hits only AWS's documented example key `AKIA…(AWS documentation example, redacted)` in tests and plan text |
| `gitleaks git --log-opts=HEAD` (main's history) | **no leaks found** |
| Not runnable / not merged | `tesseract` (host binary), spaCy-model `ner` tests, `deploy` (no tests yet; no `deploy/` directory), `tests/security` (T17, absent), `flow_report.py` / `e2e_demo.py` (T24–T26, absent), M3 `oracle`/`mongo` and the M4 scanner (not merged) |

## Adaptation probes (all through the HTTP API; output is codes, counts and booleans only)

### SEC-2: tenant-scoped secret references, plus tenancy of the M5 admin and reports routes

Logs: `logs/sec_probe.log`, **65/65**. I fixed r1's 7 probe errors in the script, so there were no false FAILs this time.

Setup: tenant B is `vrb-101157`. Its keys were minted through the admin API and held in memory only.

**Secret-reference refusals:**
- A `secret_ref` naming A's folder gets **403 `secret_ref_foreign_tenant`** for both B-jobs and B-admin. The refusal bodies are value-free.
- B's PATCH to A's ref: 403 foreign.
- `options.sslrootcert_ref` pointing into A's folder: 403 foreign.
- `_shared/none-here`: **403 `secret_ref_not_shared`**.
- 5 malformed refs (`../x`, `/abs`, `a/../../b`, `_shared/../default/x`, `dps/../../default/...`): 422.

**B's relative reference:**
- B's relative `dps/src/bank-pg-ro` resolves under B. Create returns 201.
- The connection test answers `ok:false` with `secret_unavailable`, and `/objects` answers 409.

**Tenancy:**
- B gets 404 on A's connections (GET, and admin PATCH), A's jobs, and A's evidence (JSON, HTML and PDF).
- B's lists hold none of A's connections or packs.
- `/admin/keys` shows only B-scoped keys and never a raw key or a hash.
- `/admin/tenants` shows only B, and `/admin/audit` only B's rows.
- `?tenants=default` gets 403 `tenant_forbidden`.
- `/reports/summary` with a detect-scoped B key: 200.
- Posture is value-free.

**Escalation:**
- B-admin minting a key for `default` or `*`: **403 `tenant_escalation`**.
- A B jobs-only key minting a key: 403.
- B-admin PATCHing tenant `default`: 403 `tenant_forbidden`.

**Secret scan:** the 3 raw minted keys appear 0 times in container logs and 0 times in audit/evidence rows.

### SEC-3: endpoint-defining params, including M5's oracle `options.protocol`

Logs: `logs/sec_probe.log`, `logs/sec_probe2.log` (15/15) and `logs/sec_probe2_tls.log` (19/19).

**Oracle connection with inline credentials, jobs key:**
- `options.protocol` tcp→tcps without credentials: **403 `connection_endpoint_change_requires_credentials`**, and the params are unchanged.
- `service_name` change without credentials: 403.
- `connect_timeout` change: 200.
- Protocol change with credentials: 200.
- Tagging production: a jobs key gets 403 `admin_scope_required`. An admin tagging an inline-credential connection gets 422 `connection_invalid`, by design.

**Postgres connection with inline credentials:**
- host, port, database and `options.sslmode` changes without credentials: each 403.
- The test route ignores param overrides.

**Production connection (`secret_ref`), jobs key:**
- create, PATCH (name, host, host plus ref, tags, `secret_ref`, roles) and DELETE: each **403 `admin_scope_required`**.
- The row is unchanged.
- The admin key can rename it (200) and delete it (204).

**Live TLS-required run, checking that the admin rule answers before TLS:**
- **Plain-http tokenizer.** With `DPS_EGRESS_TLS_REQUIRED=true` alone (override outside the repo), the app **refuses to boot**: `ValueError: DPS_TOKENIZER_URL must be https when egress TLS is required`. This is fail-closed and the message is value-free (`logs/app-tls-bootfail.log`).
- **Tokenizer provider emptied**, otherwise the same:
  - a jobs key sending a TLS-violating plaintext host change to a production connection gets **403 `admin_scope_required`** (not 422);
  - the admin key sending the same change gets **422 `tls_required`**;
  - a plaintext postgres create gets 422 `tls_required`;
  - an oracle create with `protocol=tcp` gets **422 `tls_required`**, and with `protocol=tcps` gets 201.
- **Restore.** The app was then restored without the override. `DPS_EGRESS_TLS_REQUIRED` is unset again, the tokenizer is back on http, and smoke passed 34/34.
- **Audit:** `connection.update` rows hold no host, ref, service name or marker.

### SEC-4 (D-B), live with the tokenizer-mock as the counting server

Log: `logs/sec4_probe.log`, **13/13**. The count is taken from the mock's own request log.

- An obfuscate with `external_tokenize` calls tokenize once.
- A legitimate `/reveal` round-trips the data with **exactly 1** detokenize batch.
- A real foreign bank token is refused with **403 `external_token_not_bound`** in three cases:
  - a forged `bind`;
  - another token's legitimate `bind`;
  - no `bind` at all.
- The mock's **detokenize count did not change (+0)**, and the refusals are value-free.
- The batch is audited (`external.detokenize`), and the audit rows are value-free.

### Discovery fix for synthesize runs (mixed-PII never kept; kept-column hits → review required)

Logs: `logs/da_probe2.log` (**17/17**) and `logs/review_chip.json`.

**Default discovery of `bank.contacts`:**
- `value` is `MIXED_PII`, and its synthesize method is `surrogate`.
- Copula and `tabular: none` runs each wrote 40,000 rows with **0** `value` values found in the source. The verdict is passed with no review.

**Owner overrides `$.value` to not personal:**
- The synthesize run keeps the column: 40,000/40,000 source values, which is why a review is required.
- The pack reads `passed: false`, `status: review_required`.
- `value` is named in `review_columns`, `post_sample.kept_columns` and `synthetic_kept.kept_columns`.
- The evidence list and the `/reports/evidence` summary read review_required and not passed.
- **Control:** an obfuscate run with the same snapshot is also review_required.

**Console**, 0 console errors:
- Reports shows the chip **"review required"** on both rows.
- The evidence page says the post-write sample found personal data in `value`.
- Job detail reads "Review required. The post-write sample found personal data in 189 values …". It is never "verified".

One-time DEKs and seeds were counted in memory: 0 in logs, 0 in audit/evidence rows.

### Identifier-check scope (`85ee587`), observed on this run

`demo_m2`'s append (`bank_perf.transactions`, synthesize) produced:
- pack `scope: written_rows`, `counterparty_iban` 9,685 checked, 0 in source, 0 issuable;
- verdict passed.

SQL (`logs/append_cmp.log`):

| Rows | Count | Source IBANs | Other |
|---|---|---|---|
| Appended (key > 900000001000) | 200,000 | **0** | `txn_id` 0 and `account_id` 0 in the source |
| Pre-existing seed rows | 1,000 | 53 | the false review the fix removes |

The r1 residual (a concurrent foreign writer) is unchanged and non-blocking. It is carried as N-b for F1.

## Programme §6 proofs: source-vs-target on every target written

### L2 synthesize (`logs/l2_synth.json`, `logs/l2_summary.txt`)

| Case | Rows | Verdict | Fidelity | Exact rows (pack / SQL non-key) | Real keys in target | Identifier check (checked/in_source/issuable) |
|---|---|---|---|---|---|---|
| copula customers | 20,000 | passed, review [] | 0.970 | 0 / 0 | `customer_id` 0; `relationship_manager_id`→employees 0 | national_id 20,000/0/0; tax_id 12,069/0/0 |
| surrogate customers | 20,000 | passed | – | – / 0 | 0 / 0 | same |
| copula accounts | 30,000 | passed | 0.985 | 0 / 0 | `account_id` 0; `customer_id` 0 | account_number 30,000/0/0; iban 2,916/0/0 |

Further results:
- NNDR passed: 1.50 for customers, 3.36 for accounts. MIA ran and utility is present. `same_value_same_key` is 0 for every column.
- Per column, values found anywhere in the source:
  - **0** for `customer_id`, `national_id`, `tax_id`, `email`, `mobile`, `notes`, `relationship_manager_id`, `account_id`, `account_number` and `iban`;
  - `full_name` 864 / 852 (name-pool overlap, M5 N1, known);
  - surrogate `created_at`/`updated_at` 20,000 (kept verbatim, M5 N2, known);
  - date, categorical, risk, balance and ifsc overlaps are chance or low cardinality.
- Cross-column: of 134,089 sensitive values, the only hits are `full_name` (N1).
- One-time key and seed scan: 0 / 0.

### Masked (obfuscate) targets (`logs/mask_cmp.log`, `logs/all_targets_cmp.log`, `logs/fpe_fixedpoint.log`)

Every `bank_uat` table that demo_m2 wrote was compared with its source, column by column.

**0 values found in the source** for every personal column:
- customers: `full_name`, `national_id`, `tax_id`, `email`, `mobile`, `notes`;
- accounts: `account_number`, `iban`;
- addresses: `line1`, `postal_code`;
- cards: `pan`, `cardholder_name`;
- contacts: `value`;
- employees: `full_name`, `email`, `phone`;
- kyc_documents: `doc_number`;
- loan_applicants: `full_name`, `pan_or_ssn`;
- transactions: `counterparty_name`, `counterparty_iban`.

The same holds for `customers_console` and `customers_smoke`.

**FPE customer-id collisions** (7 / 3 / 5 rows in customers / customers_console / customers_smoke): none is a fixed point. 0 of those rows share both `created_at` and `date_of_birth` with the source row (N9, known).

**Kept unchanged by the obfuscate path:**
- `relationship_manager_id` and `kyc_documents.verified_by` (employee ids; integrator open issue 3, pre-existing);
- `employees.employee_id` and other internal surrogate keys (`account_id`, `address_id`, `card_id`, `txn_id`, `loan_id`, `applicant_id`, `doc_ref`);
- `kyc_documents.notes`, which is a non-personal template ("Checked against the original in branch NNN; copy on file", per `tools/seed_sources.py`).

All of this is pre-existing M2 obfuscate behaviour, not introduced by M5.

**In-place `bank_restore.contacts`:** 40,000 rows, **0** values in the source, 0 same-id-same-value. The other `bank_restore` tables equal `bank`, as they should, since they were not targets.

**Append `bank_perf.transactions`:** see the identifier-check section above.

### Log, audit and evidence scans (`logs/scan1.log`, `logs/scan2.log`, `logs/scan3.log`)

Scope:
- every main container log (151 KB after the restore);
- the three saved app logs (pre-TLS 198 KB, TLS boot failure, TLS run 15 KB);
- `dps.dps_audit_log` (329 rows) and `dps.dps_evidence` (35 rows).

Needles:
- about 237K distinct demo values of 8 characters or more from 20 columns (customers, accounts, contacts, employees, cards, addresses, kyc_documents, transactions);
- the KEK (base64 and decoded);
- the bootstrap and tokenizer literals;
- the `dps-secrets` password and secret-key file contents (read in memory);
- key-shaped `dps_…` tokens.

Results:
- **0 source values in every log, audit row and evidence row.** The positive control was detected (4 ≥ 3).
- 0 key-shaped tokens.
- 0 hits of anything in the app and worker logs.
- The only hits are third-party server lines in demo containers. Per-container attribution was printed with values masked:
  - The demo DB logins use username = password; 3 of 3 password files equal their sibling username.
  - `source-pg`: 9 hits, all 9 inside `user "…"` of `password authentication failed` lines from tests that probe wrong passwords.
  - `dps-db`: 2 auth-failure lines naming the user `dps_owner`.
  - `minio`: its own default-credentials startup warning (×1 line).
- **The probes' raw keys, DEKs and seeds** were each counted in-process: 0 in logs, 0 in audit/evidence.
- **LLM counter:** `dps_llm_raw_pii_sent_total` is 0.0 on the app (:8400) and on the worker (:9400). No per-tenant series exist; per the DoD they appear only when non-zero.

**D4 counting server:** not applicable; the M4 scanner is not merged. The tokenizer-mock served as the counting server for SEC-4.

## Console walk (private Playwright 1.55.0 from the fresh venv)

**Sweep** (`logs/walk.json`):
- 14 distinct routes, covering every route in `App.tsx`, with real job, evidence and policy ids, plus the 404 page.
- Variants: 1440 light, 1440 dark and 390 light, for 42 visits.
- **0 console errors, 0 failed responses, 0 pages with horizontal scroll at 390 px.**

**Admin** (`admin_walk.json`):
- Create shows the raw key once. After "stored", the key is absent from the DOM, storage and the URL.
- Rotate (24 h grace) leaves 2 rows. Rotate "now" marks the old key "rotated, disabled".
- Disable and re-enable work. A bad name is refused inline.
- Tenant add works. Hosts: an invalid host is refused; Remove works.
- Posture shows "5 of 11 pass" (local, by design).
- 0 errors and 0 bad responses. Raw keys: 0 in logs, 0 in audit/evidence.

**Policies** (`policies_walk.json`):
- Adopt `dpdp-pseudonymise` → v1. Live test: "1 match · 1 on the allow list".
- Draft v2 with 16 changes. Plan: 12 findings.
- Publish; the diff shows v1 → v2 with 2 additions. The YAML downloads with the recognizer.
- 0 errors.

**Reports & evidence** (`reports_walk.json`):
- KPIs present ("Customer values stored 0").
- CSV, roll-up JSON/PDF and pack JSON/PDF download (`%PDF-`).
- Filters live in the URL. Audit "Load 50 older" goes 50 → 100. Search finds 1 row.
- The copula evidence page has 8 sections, including "6 Synthetic fidelity and privacy".
- 0 errors.

**Session key in the DOM** (`logs/key_dom.json`): the walk's `key_in_dom` flag fires on every page. That is r1's observation, now pinned down:
- the key appears exactly once in the HTML, as the `value` of the header's `type=password` input (the operator's own session key);
- 0 times in page text and 0 times in the URL.

This is not a leak. One small accessibility nit: that input has no id, name or aria-label of its own, though it sits under the visible "API KEY" label.

## Route-auth walk (`logs/route_auth.log`)

- Coverage: all 62 `/api/v1` paths from the OpenAPI document, 71 operations.
- With no key and with a well-formed wrong key: **142/142 answered 401**, none otherwise.
- The hidden NDJSON routes carry `require_scope` (code read).
- `/health`, `/readyz` and `/metrics` are public ops routes by design.
- `/api/v1/openapi.json` answers without a key in local dev; posture turns docs off in prod.

## Non-blocking notes

- **N-a: gitleaks working-tree scan.** `gitleaks dir` flags 12 tracked test fixtures and plan snippets (`generic-api-key`, `jwt`, `curl-auth-header`). The redacted contexts show test constants and documentation examples. Main's history scan is clean, because `.gitleaksignore` (`afd5998`) holds commit fingerprints. F1/F5 may want path-based allowlisting so that `gitleaks dir` is also clean.
- **N-b: identifier-check residual.** The concurrent-writer case, unchanged from r1, for F1.
- **N-c: TLS boot refusal.** Confirmed again: a plain-http tokenizer under `DPS_EGRESS_TLS_REQUIRED=true` stops the app at boot. F2's staging and prod overlays must ship an https tokenizer URL or an empty provider.
- **N-d: verbatim columns in surrogate synthesize.** A surrogate synthesize (`tabular: none`) keeps non-personal measure columns verbatim. In the demo append, `amount` and `booked_at` match the source on all 200,000 rows. That follows D-A as written (non-personal columns kept when the post-write sample is clean) and extends the known N2. If the owner wants surrogate synthesize to break row-level linkability through non-personal columns, the owner should decide that in F1. The copula path does break it (exact non-key rows 0).
- **N-e: one gitleaks hit on the m3 branch, not on main.** With `--all`, gitleaks reports one hit in `ba60834` (`HANDOFF.md:904`). It is a compose default in the FPE paragraph. Whoever does the M3 merge should add a reviewed fingerprint for it.
- **Carried, unchanged:**
  - N1 (name pool);
  - N2 (surrogate timestamps);
  - N9 (FPE onto issued ids);
  - integrator open issue 3 (`relationship_manager_id` / `verified_by` kept by obfuscate);
  - N5 (single-value demo columns);
  - A-N1 (`var/demo` group file; I used `--group im5-vr2`).
- **Side effects on the stack** (main compose volumes only; no repo files):
  - tenants `vrb-101157` and `im5-walk-101656`;
  - about 12 probe keys (1-day expiry);
  - published policy `dpdp-pseudonymise` v2;
  - `bank_uat.v_*` probe tables;
  - `var/demo/im5-vr2.json` (git-ignored).
