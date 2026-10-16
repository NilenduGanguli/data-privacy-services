# WP-I (m5) — independent verification of main after the M5 merge, round 1

**Verdict: FAIL.** There are 2 blocking gate failures. Both are small and appear only with the `synth-ml` extra installed. This was the first gate run with every extra, so they are latent M5-lane defects; the integrator did not introduce them.

Everything else passed:
- every other gate;
- every integration marker;
- the frontend;
- smoke;
- `demo_m2`;
- the SEC-2/SEC-3/SEC-4 and discovery-adaptation probes;
- the source-vs-target comparisons;
- the console walk;
- the log scans.

## Scope and start state

- Working copy: `/Users/neelu/document_intelligence/data-privacy-services`, branch `main`, HEAD `709f69a`.
  - The tree was clean before and after. I changed no repo file and made no commits or tags.
  - The resume check found no earlier `I-m5-verify-r1.md`, so this is a fresh run.
- Scratch: `~/.cache/dps-build/i-m5-vr1/`:
  - `probe/` (scripts)
  - `logs/` (every gate and probe output)
  - `shots/` (42 walk screenshots plus the review pages)
  - `override-tls.yml` (outside the repo)
- Fresh venv: `~/.cache/dps-build/venv-I-m5-1`.
  - Built with `uv venv -p 3.12` and `uv pip install -e ".[ner,fpe,vertex,docs,db,connectors,uitest,e2e,tabular,synth-ml,dev]"`.
  - That is **every** extra, including `synth-ml` (mostlyai and torch), which the integrator left out.
- Stack, rebuilt from HEAD on clean volumes:
  - `docker compose --env-file docker/compose.sources.env --profile full down -v`
  - then `DPS_TOKENIZER_MOCK_PORT=8430 … --profile full up --build -d`
  - Default ports 8400/5441/5442/9010/9011/5016; tokenizer-mock on 8430.
  - `/readyz`: ready. db (migrations verified), jobs, fpe ff1, tokenizer http, ocr azure-read-v3.2 and keys db+static are ok. ner is patterns-only, as expected.
- The stack is **left running** in its default configuration. The app was restored after the TLS probe, and a smoke run afterwards passed 34/34.
- No `dps-wt-*` worktree or container was touched.

## Blocking findings

1. **mypy fails with every extra installed.**
   - Location: `dps/engine/synth/mostly.py:125`, error `Item "dict[str, Any]" of "Any | dict[str, Any]" has no attribute "to_dict"  [union-attr]`.
   - With `mostlyai` installed, the SDK types `SyntheticDataset.data()` as `DataFrame | dict[str, DataFrame]`.
   - With `.venv`, which has no synth-ml, mypy passes (128 files). This is the same class of latent issue the integrator fixed for `vertex` in `0bd6977`.
   - Fix: narrow the type explicitly, failing closed. For example:

     ```python
     frame = mostly.generate(...).data()
     if isinstance(frame, dict):
         frame = frame["rows"]
     if not isinstance(frame, pd.DataFrame):
         raise TabularUnavailable(...)
     ```

     Then re-run `mypy dps` in a venv that has `synth-ml`.
2. **`tests/connectors/test_targets.py::test_files_synthesize_group_and_tabular_rules` fails when the `synth-ml` extra is installed.**
   - The failing line is 351: `assert _code(mostly, with_mostly) == ("tabular_invalid", 422)  # the extra is not installed`. With the extra installed, nothing is raised (`DID NOT RAISE SpecError`).
   - It passes in `.venv`, which lacks the extra. So the test depends on the environment.
   - Fix: make the assertion independent of the installed extras. Monkeypatch `dps.engine.synth.mostly.mostly_available` to return `False` for the refusal branch, and add the positive branch (`True` means accepted) so both are covered on every machine.
   - Full-suite result with every extra: **1 failed, 2070 passed, 4 skipped**.

## Gates (fresh venv, every extra, main stack up, contract §12.2 `DPS_TEST_*` exported)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` | 311 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | **FAIL: 1 error** (B1). Passes without `synth-ml` |
| `pytest -q -rs` (after smoke) | **1 failed (B2)**, 2070 passed, 4 skipped. Skips: tesseract binary ×2 (host), spaCy English model ×2 (the `ner` extra installs no model). The mostlyai tests now run |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "docs or ocr"` | 139 passed, 2 skipped (tesseract binary) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "db or pg_source or s3"` | **294 passed**, 0 skipped |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m browser` | 3 passed (no tracked file changed) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "ml or slow or fpe"` | **18 passed, 0 skipped**. This includes `test_mostly.py` and `test_mostly_linked.py` with the real MOSTLY AI local engine, and resume-after-kill |
| M5 DoD files (`test_llm_egress`, `test_mostly_linked`, `test_posture_m5`), services required | 49 passed, 0 skipped |
| `frontend: npm ci && npm test && npm run typecheck && npm run build` | node 42/42; Vitest 201/201 (14 files); tsc clean; build ok |
| `DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py` | all 34 checks passed (fresh stack). Also 34/34 again at the end, after the TLS override was removed |
| `python tools/demo_m2.py --group im5-vr1` | 13 steps succeeded, every verification passed, referential check ok, 0 orphans on 11 FKs |
| Markers not runnable / not merged | `tesseract` (host binary), `ner` model tests (no spaCy model), `deploy` (no tests yet, T25), M3 `oracle`/`mongo` and M4 scanner (not merged) |

## Adaptation probes

### SEC-2: tenant-scoped secret references, plus tenancy of the M5 admin and reports routes

Logs: `logs/sec_probe.log` (62/69) and `logs/sec_probe2.log` (15/15).

The 7 "FAIL" lines in `sec_probe.log` are probe errors that `sec_probe2` corrected; none is a product defect:
- `/reports/summary` needs the `detect` scope. A detect key gets 200, and a jobs-only key gets 403 by design.
- A production connection cannot hold inline credentials (`connection_invalid`, 422, by design). Because of that, my first production sub-probe never had a production row to test against. I redid it with a `secret_ref` production connection (below).

Setup:
- Tenant B (`vrb-014149`) and three keys were minted through the M5 admin API: A-jobs, B-jobs, and B-admin (jobs+admin+detect).
- The raw keys were held in memory only. In-process count: 0 in container logs, 0 in audit/evidence rows.

Secret-reference refusals:
- A `secret_ref` whose first segment names A's folder gets **403 `secret_ref_foreign_tenant`** for both B-jobs and B-admin. The refusal bodies are value-free.
- PATCH to A's ref: 403. `options.sslrootcert_ref` pointing into A's folder: 403.
- An unlisted or nonexistent `_shared/` ref: **403 `secret_ref_not_shared`**.
- Malformed refs (`../x`, `/abs`, `a/../../b`, `_shared/../default/x`, `dps/../../default/...`): 422.

B's relative reference:
- B's relative `dps/src/bank-pg-ro` resolves under B's own folder. Create returns 201.
- The connection test answers `ok:false` with `secret_unavailable`, and `/objects` answers 409. B cannot use A's secret.

Tenancy:
- B gets 404 on A's connections (GET, and admin PATCH), A's jobs, and A's evidence (JSON, HTML and PDF).
- B's lists hold none of A's connections or packs.
- `/admin/keys` returns only B-scoped keys and never a raw key or a hash.
- `/admin/tenants` returns only B.
- `/admin/audit` returns only B's rows. `?tenants=default` gets **403 `tenant_forbidden`**.
- Posture is value-free.

Escalation:
- B-admin minting a key for `default` or `*`: **403 `tenant_escalation`**.
- A B jobs-only key minting a key: 403.
- B-admin PATCHing tenant `default`: 403 `tenant_forbidden`.

### SEC-3: endpoint-defining params, including M5's oracle `options.protocol`

Logs: `logs/sec_probe.log`, `logs/sec_probe2.log` and `logs/sec_probe2_tls.log` (19/19).

Oracle connection with inline credentials, jobs key:
- `options.protocol` tcp→tcps without credentials: **403 `connection_endpoint_change_requires_credentials`**, and the params are unchanged.
- `service_name` change without credentials: 403.
- `connect_timeout` change (not endpoint-defining): 200.
- Protocol change *with* credentials: 200.

Postgres connection with inline credentials, jobs key:
- host, port, database and `options.sslmode` changes without credentials: each 403.
- The test route ignores param overrides (`ok:true` on the good connection).

Production connection (`secret_ref`), jobs key:
- create, PATCH (name, host, host plus ref, tags, `secret_ref`, roles) and DELETE: each **403 `admin_scope_required`**.
- The row (name, params, ref, tags, roles, `updated_at`) is unchanged.
- The admin key can rename it (200) and delete it (204).

**"Admin rule answers before TLS"**, checked live:
- I recreated `app` with an out-of-repo override: `DPS_EGRESS_TLS_REQUIRED=true` and `DPS_TOKENIZER_PROVIDER=""`.
- The first attempt, with the tokenizer still on plain-http, **refused to boot**: `DPS_TOKENIZER_URL must be https when egress TLS is required`. That is fail-closed and the message is value-free.
- With TLS required:
  - a jobs key sending a TLS-violating plaintext host change to a production connection gets **403 `admin_scope_required`**, not 422;
  - the admin key sending the same change gets **422 `tls_required`**;
  - a plaintext postgres create gets 422 `tls_required`;
  - an oracle create with `protocol=tcp` gets **422 `tls_required`**, and with `protocol=tcps` gets 201.
- The app was then restored without the override (smoke 34/34 afterwards).

Audit rows for `connection.update` contain no host, ref, service name or marker.

### SEC-4 (D-B), live with the tokenizer-mock as the counting server

Log: `logs/sec4_probe.log`, 13/13. The count is taken from the mock's own request log.

- An obfuscate with `external_tokenize` calls the mock's tokenize endpoint once.
- A legitimate `/reveal` round-trips the data with **exactly 1** detokenize batch.
- A real bank token for a foreign value, obtained from the mock directly, is refused with **403 `external_token_not_bound`** in three cases:
  - a forged `bind`;
  - another token's legitimate `bind`;
  - no `bind` at all.
- The refusals are value-free, and the mock's **detokenize count did not change (+0)**.
- The batch is audited (`external.detokenize`), and the audit rows hold no SSN and no `tk_` token.

### Discovery fix for synthesize runs (mixed-PII never kept; kept-column hits → review required)

Log: `logs/da_probe2.log`, 17/17, through the HTTP API.

**Default discovery of `bank.contacts`:**
- `value` is `MIXED_PII`, and its synthesize method is `surrogate`, never `keep`.
- Synthesize to `bank_uat`, copula and `tabular: none`: each run wrote 40,000 rows with **0** `value` values found in the source. The verdict is passed with no review.

**Owner overrides `$.value` to not personal, then runs synthesize:**
- The job succeeds. The kept column carries 40,000/40,000 source values; that is exactly why a review is required.
- The pack reads `passed: false`, `status: review_required`, `review_required: true`.
- `value` is named by both checks: `review_columns`, `post_sample.kept_columns` and `synthetic_kept.kept_columns`.
- The `/evidence` list and the `/reports/evidence` summary read `review_required: true` and `verification_passed: false`.
- **Control:** an obfuscate run with the same snapshot is also `review_required`.

**Console** (`logs/review_chip.json`, 0 console errors):
- Reports shows the chip **"review required"** on both rows.
- The evidence page explains that the post-write sample found personal data in `value`.
- Job detail reads "Review required. The post-write sample found personal data in 187 values …". It is never "verified".

One-time DEKs and synthesis seeds were counted in memory: 0 in logs, 0 in audit/evidence rows.
- My first attempt counted every key-material string, including the public `alg`, `kid` and `kcv`. That is a probe bug, not a leak; the corrected re-run counts only `dek` and `seed`.

### Identifier-check scope (`85ee587`), reviewed and observed

`demo_m2`'s append (`bank_perf.transactions`, synthesize) produced:
- pack `scope: written_rows`, `counterparty_iban` 9,685 checked, 0 in source, 0 issuable;
- verdict passed;
- SQL over keys > 900000001000 (the appended rows): **0** source IBANs. The 1,000 pre-existing seed rows hold 53. That pre-existing overlap is the false review the fix removes.

Code review: the fallback to `whole_table` triggers when fewer rows follow the floor than the run wrote. That is sound as a fail-closed rule.
- **Residual (non-blocking, for F1):** concurrent foreign inserts into the same target during the run could make the count reach `expected_rows` while some written rows sit below the floor. For example, with an offset `pk_strategy` whose keys land below an existing higher key. Rows below the floor would then go unchecked. It needs a concurrent writer on a UAT table, and it fails open only for those rows.
- Possible hardening: compare against the written rows' own key set or range, rather than a count.

## Programme §6 proofs

### L2 synthesize (`logs/l2_synth.json`, `logs/l2_summary.txt`)

Setup: HTTP API, `bank-pg-ro` → `bank-uat`, three cases.

| Case | Rows | Verdict | Fidelity | Exact rows (pack / SQL non-key) | Real keys in target | Identifier check (checked/in_source/issuable) |
|---|---|---|---|---|---|---|
| copula customers | 20,000 | passed, review [] | 0.978 | 0 / 0 | `customer_id` 0, `relationship_manager_id`→employees 0 | national_id 20,000/0/0; tax_id 12,069/0/0 |
| surrogate customers | 20,000 | passed | – | – / 0 | 0 / 0 | same |
| copula accounts | 30,000 | passed | 0.984 | 0 / 0 | `account_id` 0, `customer_id` 0 | account_number 30,000/0/0; iban 2,916/0/0 |

Further results:
- NNDR passed: 1.49 for customers, 3.41 for accounts. MIA ran and utility is present.
- Per column, values found anywhere in the source:
  - **0** for `customer_id`, `national_id`, `tax_id`, `email`, `mobile`, `notes`, `relationship_manager_id`, `account_number` and `iban`;
  - `full_name` 872 / 839 (name-pool overlap on unrelated rows; M5 N1, known);
  - surrogate `created_at`/`updated_at` 20,000 (kept verbatim; M5 N2, known);
  - date, categorical, balance and ifsc overlaps are chance or low cardinality.
- `same_value_same_key` is 0 for every column.
- Cross-column: of 134,089 sensitive demo values, the only hits are `full_name` (N1).

### Masked (obfuscate) targets (`logs/mask_cmp.log`, `logs/inplace_cmp.log`)

Targets: `bank_uat.customers` (demo_m2), `customers_console` and `customers_smoke`.
- `full_name`, `national_id`, `tax_id`, `email`, `mobile` and `notes`: **0** in the source.
- `customer_id`: 4 / 5 / 3. FPE landed on another real id; `created_at` differs on those rows, so these are not fixed points (N9, known).
- `relationship_manager_id`: all values are kept. This is the integrator's open issue 3, pre-existing.
- In-place `bank_restore.contacts`: 40,000 rows, **0** values in the source, 0 same-id-same-value.

### Log and DB scans (`logs/scan1.log`, `logs/scan2.log`)

Scope:
- every main container log, about 308 KB before the TLS restart;
- the three saved app logs (pre-TLS, TLS boot failure, TLS run);
- `dps.dps_audit_log` (295 rows) and `dps.dps_evidence` (34 rows).

Needles:
- 237K distinct demo values of 8 characters or more from 20 sensitive columns;
- the KEK (base64 and decoded);
- the DB role passwords;
- the bootstrap and tokenizer literals;
- every password and secret-key file in `dps-secrets` (read in memory);
- key-shaped `dps_…` tokens;
- `tk_` tokens;
- the SEC-4 probe SSNs and the smoke-test PII literals.

Results:
- **0 source values** in any log, audit row or evidence row. The positive control was detected (4 ≥ 3).
- 0 key-shaped tokens, 0 `tk_` tokens, 0 probe or smoke literals.
- The only hits are third-party server lines, all in demo containers:
  - The demo's DB and MinIO logins use username = password. I confirmed equality in memory, without printing.
  - Postgres auth-failure lines name the *user* (`source-pg` ×6, `dps-db` ×2 for `dps_owner`). These come from tests that probe wrong passwords.
  - MinIO's own default-credentials startup warning (×2).
  - None of these hits is in the app or the worker.

**LLM counter:** `dps_llm_raw_pii_sent_total` is 0.0 on the app and on the worker (:9400).

**D4 counting server:** not applicable; the M4 scanner is not merged. The tokenizer-mock served as the counting server for SEC-4.

## Console walk (private Playwright 1.55.0 from the fresh venv; `logs/walk.json` and the walk logs)

**Sweep:**
- Pages: 14 routes, covering every route in `App.tsx` with real job, evidence and policy ids, plus the 404 page.
- Variants: 1440 light, 1440 dark and 390 light, for 42 visits.
- Result: **0 console errors, 0 failed responses, 0 pages with horizontal scroll at 390 px**.

**Admin** (`admin_walk.json`):
- Create shows the raw key once. After "stored", the key is absent from the DOM, storage and the URL.
- Rotate (24 h grace) leaves 2 rows. Rotate "now" marks the old key "rotated, disabled".
- Disable and re-enable work. A bad name is refused inline.
- Tenant add and quota edit work. Hosts are normalised; an invalid host is refused; Remove works.
- Posture shows "5 of 11 pass" (local, by design).
- 0 errors. Raw keys: 0 in logs, 0 in audit/evidence.

**Policies** (`policies_walk.json`):
- Adopt `dpdp-pseudonymise` → v1. Live test: "1 match · 1 on the allow list".
- Draft v2 with 16 changes. Plan: 12 findings. Publish v2 with a ticket; the diff shows 2 additions. The YAML downloads with the recognizer.
- 0 errors.

**Reports & evidence** (`reports_walk.json`):
- KPIs present. CSV, roll-up JSON/PDF and pack JSON/PDF download (`%PDF-`).
- Filters live in the URL. Audit "Load 50 older" goes 50 → 100. Ticket search finds 1 row.
- The copula evidence page has 8 sections, including "6 Synthetic fidelity and privacy".
- 0 errors.

**Observation (not a leak):** the operator's own API key, typed into the header's password field, appears as that input's `value` attribute in `page.content()`. It is not in the page text. This is a React controlled input showing the caller's own session key.

## Non-blocking notes

- **N-a: one-shot health status.** The one-shot containers (`seed-sources`, `migrate`, `demo-secrets`) inherit the app image's HEALTHCHECK. After exiting 0, `docker ps` shows them "unhealthy". Cosmetic; F2 could set `healthcheck: {disable: true}` on one-shots.
- **N-b: identifier-check residual.** The concurrent-writer case described above (`85ee587`), for F1.
- **N-c: TLS boot refusal is all-or-nothing.** With `DPS_EGRESS_TLS_REQUIRED=true`, a plain-http tokenizer URL stops the whole app at boot. That is correct fail-closed behaviour, but F2's staging and prod overlays must ship an https tokenizer URL (or an empty provider), or the pods crash-loop.
- **Carried, unchanged:** N1 (name pool), N2 (surrogate timestamps), N9 (FPE onto issued ids), integrator open issue 3 (`relationship_manager_id` kept by obfuscate), N5 (single-value demo columns), A-N1 (`var/demo` group file; I used `--group im5-vr1`).
- **Side effects on the stack** (all in the main compose volumes; no repo files):
  - tenants `vrb-014149` and `im5-walk-015954`;
  - about 12 probe keys (1-day expiry);
  - published policy `dpdp-pseudonymise` v2;
  - `bank_uat.v_*` probe tables;
  - `var/demo/im5-vr1.json` (git-ignored).
