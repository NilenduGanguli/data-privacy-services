# WP-H step H2 — independent verification r1 (main since v0.3.0-m2)

**Verdict: PASS.** Nothing blocks. Every H2 probe passed through the running API. Every gate is green.

- Working copy: `/Users/neelu/document_intelligence/data-privacy-services` on `main`, HEAD `4615e3d`. The tree was clean before and after the run. No file in the repo changed and nothing was committed.
- Scope (`git log v0.3.0-m2..HEAD`), 8 commits:
  - `e25209f` SEC-2
  - `34f4aa3` SEC-3
  - `6ed65be` N3
  - `9fa6476` wizard step 5 server check
  - `b471266` length-safe method suggestion and pre-run refusal
  - `9d19f7b` fallback label prefix
  - `2b536a7` plan (docs only)
  - `4615e3d` H1 console text
- Stack: `docker compose --profile docs --profile sources up --build -d`, with default ports and project `data-privacy-services`. It was built from HEAD and every service reported healthy; `/readyz` answered ready.
- Afterwards I ran `docker compose --profile docs --profile sources down`. Volumes are kept.

All artefacts are in `~/.cache/dps-build/h2r1/`:
- probe scripts: `probe/`
- logs: `*.log`
- screenshots: `shots/`

## Deviations from the literal instructions (none affect the verdict)

1. **Extra API keys for the probes.** SEC-2 and SEC-3 need keys with only the `jobs` scope, and keys for a second tenant. The compose stack has only the wildcard bootstrap key.
   - I recreated `app` with a compose override that lives outside the repo (`~/.cache/dps-build/h2r1/override.yml`). It adds `DPS_API_KEYS` with three `key_sha256` records:
     - `h2-a-jobs`: tenant `default`, scope `[jobs]`
     - `h2-b-jobs`: tenant `tenantb`, scope `[jobs]`
     - `h2-b-admin`: tenant `tenantb`, scopes `[jobs, admin]`
   - The raw keys are kept only in `probe/keys.json` (mode 600).
   - The bootstrap key behaves exactly as before. All gates ran against this app.
2. **Probe secret folders.** I wrote `tenantb/dps/src/b-own`, two symlink folders and `_shared/h2-ro` (with a `tenants` allow-list) into the `dps-secrets` volume. I removed them again (`/s` now holds only `default/`).
3. **Stale demo file (known A-N1).** `tools/demo_m2.py` with the default group failed with `HTTP 404 group_not_found`. The cause is `var/demo/bank-uat-refresh.json`, a group file left over from an older metadata DB. I re-ran with `--group bank-uat-refresh-h2r1`, which wrote a new git-ignored file under `var/demo/`.
4. **`tools/console_check.py` not run.** It is not in the H2 gate list, and it overwrites tracked screenshots (known A-N2), which would modify files.
5. **Private Playwright venv.** `~/.cache/dps-build/pw-main` did not exist, so I created it (`uv venv` plus `playwright==1.55.0`; the Chromium build was already cached).

## Gates

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` | 252 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success: no issues in 103 source files |
| `cd frontend && npm run typecheck && npm test && npm run build` | typecheck exit 0 · node 42/42 · Vitest 69/69 (2 files) · build ok |
| `DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py` | 34/34 |
| `pytest -q` (stack up, DPS_TEST_* exported) | 1714 passed, 4 skipped, 1 warning (starlette/httpx deprecation) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "docs or ocr"` (M1) | 139 passed, 2 skipped |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"` (M2) | 180 passed, 0 skipped (the console tests included) |
| `pytest -q -m slow tests/connectors/test_resume_after_kill.py` | 6 passed |
| `python tools/demo_m2.py --group bank-uat-refresh-h2r1` | 12 jobs succeeded, all verification passed, referential check ok, 0 orphans on 11 foreign keys |
| Compose logs scan (1688 lines, the seeder excluded) | 0 hits on 15 597 source needles (names, e-mails, national ids, PAN, account numbers, address lines). 0 hits on the probe keys, the bootstrap key, the KEK and the demo group key or seed |
| `dps_llm_raw_pii_sent_total` (app and worker `/metrics`) | 0 / 0 |

## Probes

### SEC-2: tenant-scoped secret references (`sec_probe.log`, 29/29)

Refusals:
- Tenant B creating a connection whose `secret_ref` names tenant A's folder gets 403 `secret_ref_foreign_tenant`. The reverse (A naming B's folder) gets the same.
- B's admin key gets the same 403. Scope does not widen the namespace.
- PATCHing B's own connection to A's ref gets 403. The stored ref is unchanged afterwards.
- An `options.sslrootcert_ref` pointing into A's folder gets 403 `secret_ref_foreign_tenant`.
- Every refusal body is value-free: none contains a ref, a tenant id or a username.

Malformed references:
- `../x`, `/abs`, `a/../../b`, `dps/../../default/...` and `_shared/../default/x` are all refused with 422 on create.
- `../x`, `/abs` and `a/../../b` are also refused with 422 on PATCH.

Use:
- B's own ref (`dps/src/b-own`) gets 201 and its connection test answers `ok: true`.
- B naming A's *relative* path (`dps/src/bank-pg-ro`) resolves under B's own folder, where nothing exists. Create returns 201, but the test answers `ok: false` with `secret_unavailable`, and `/objects` answers 409 `secret_unavailable`. B cannot use A's secret.

Symlinks:
- A ref whose folder is a symlink into A's folder gets 422 `secret_ref_invalid`.
- A folder whose files are symlinks into A's folder: create succeeds, but using it fails with `secret_unavailable`.

Shared references:
- `_shared/h2-ro` works for listed tenant A (201, test ok).
- Unlisted B gets 403 `secret_ref_not_shared` on create and on PATCH. A shared ref that does not exist gets the same 403.
- After B is added to the `tenants` file, B gets 201 and its test is ok.
- After B is removed from the file again, using the existing connection fails with `secret_unavailable`.

### SEC-3: production-connection change gate (`sec_probe.log` plus a re-probe)

Production connection, jobs-scope key:
- PATCH of host, host plus restated `secret_ref`, tags, credentials, `secret_ref`, name and roles: each gets 403 `admin_scope_required`.
- DELETE gets 403 `admin_scope_required`.
- After the refusals, the row's name, params, `secret_ref`, tags, roles and `updated_at` are unchanged.
- Tagging a non-production connection `production` with the jobs key gets 403.

Non-production connection with inline credentials, jobs-scope key:
- Changing host, port, database or `options.sslmode` without credentials gets 403 `connection_endpoint_change_requires_credentials`. The host and the sealed blob (compared by sha256 digest) are unchanged.
- A rename gets 200.
- A host change *with* credentials gets 200. The sealed blob digest changed, so the old sealed secret is gone.

Non-production connection with a `secret_ref`:
- A host change without restating the ref gets 403 `…requires_credentials`. Restating the ref gets 200.

Admin key: PATCH of the production host gets 200, and DELETE gets 204.

Test route: it takes no parameter overrides. An extra `params` key is ignored:
- A good connection plus an `evil.invalid` override still answers `ok: true`.
- A connection with a bad host plus a valid override answers `connection_failed`.

My first probe flagged this as a failure because I expected a 422. The re-probe shows the override is ignored, which is the specified behaviour.

Audit rows:
- 50 `connection.*` rows; none contains a host, username, ref, port value or the moved-host marker.
- The 20 `connection.update` rows have keys `field.<name>` only (`field.params.host`, `field.params.options.sslmode`, `field.credentials`, …) plus the generic integer counters `duration_ms`, `findings_total` and `reversible_values`. Every value is an int.
- 15 refused (403) updates are audited.

### Wizard step 5 server check

API (`run_probe.log`, `run_probe2.log`):
- An append through the read-only `bank-pg-ro`: `/jobs/run/validate` and `/jobs/run` both answer 422 `connection_role`.
- An offset strategy with no offset: validate answers 422.
- In place on a production read-write connection with the jobs key: validate and run both answer 403 `admin_scope_required`. The same spec with admin answers `{ok: true}`.
- A valid table copy answers `{"ok": true}` with `Cache-Control: no-store` and no key.
- The `dps_job` count is unchanged across every validate call and every refused run (131 → 131).

Private Playwright, 1.55.0 (`wizard.log`, 11/11, screenshots `shots/wizard-step5-*.png`):
- Path: `bank-restore-rw` → `bank_restore.customers` → discovery, with the key kept and length-safe methods → accept → Append to the same table, Key offset `0` → confirmed → step 5.
- Step 5 shows "DPS refuses this run: target.pk_strategy.offset: Value error, pk_strategy offset needs exactly one of offset or prefix". The chip reads "1 to resolve", and Run job and Dry run are both disabled. Validate answered 422.
- Offset `abc` is refused the same way.
- A valid offset `900000000` turns the chip to "all checks passed" and enables Run job.
- Run job was never pressed. The counting listener saw **0** `POST /api/v1/jobs/run` requests and 3 validate requests. There were 0 page errors.

### N3: a failed or canceled run never reads as passed (`run_probe*.log`, `n3_console.log`)

**Table copy into an existing table that is too narrow** (`national_id varchar(4)`, `email varchar(5)`, primary key copied):
- The job fails at prepare with `length_overflow`, 0 rows written.
- The `GET /evidence` row has `passed: false` and `error_code: length_overflow`.
- The pack has outcome `failed`, `verification.status: incomplete`, `passed: false`.
- The printable page contains "not verified · job failed (length_overflow)".
- **Variant without the primary key:** fails with `target_invalid`. Same result: `passed: false` and `incomplete`.

**Canceled running copy:** the job ends `canceled`. The list row has `passed: false`, the pack has `status: incomplete`, and the printable page is not "verified".

**Whole evidence store:** of 62 packs, including older ones from before the fix, none has a failed or canceled outcome with `passed: true`.

**Console** (private Playwright):
- The evidence page for a `length_overflow` pack says "not verified" and explains the error in words.
- Reports shows the chip "not verified · job failed (length_overflow)".
- There were 0 page errors.

### Length-safe method suggestion and pre-run refusal (`inplace_probe.log`, 9/9)

- Discovery of `bank_restore.customers` (as proposed) flags `national_id` (`det_token` into varchar(18)) and `tax_id` (PAN `fpe_text` into char(10)) as `type_note: length_overflow`, i.e. "Needs a decision".
  - This matches the commit's design: `national_id` holds non-digit ids, so it is flagged rather than switched to `fpe_digits` automatically.
- With only the key kept, both `/jobs/run/validate` and `/jobs/run` answer 422 `length_overflow` and name both columns. No job is created.
- After the decisions the console asks for (`national_id` → `fpe_digits`, `tax_id` → `mask_partial`, `customer_id` kept):
  - validate answers ok.
  - **In place `bank_restore.customers` succeeded**: 20 000 rows written, evidence `passed`, 0 hits, no `length_overflow` error.
- Source vs target, joined on `customer_id`, counting non-null originals that are still equal:

| Column | Before (equal / rows) | After (equal / rows) |
|---|---|---|
| `full_name` | 20000 / 20000 | 0 / 20000 |
| `date_of_birth` | 20000 / 20000 | 0 / 20000 |
| `national_id` | 20000 / 20000 | 0 / 20000 |
| `tax_id` | 12069 / 20000 | 0 / 20000 |
| `email` | 20000 / 20000 | 0 / 20000 |
| `mobile` | 20000 / 20000 | 0 / 20000 |
| `notes` | 5980 / 20000 | 0 / 20000 |

  The row count is unchanged at 20 000. Before the run, every non-null value equalled the original (`tax_id` and `notes` have nulls), so the comparison was valid.

### Fallback label (`fallback.log`)

Jobs from this session (the demo plus the probes):
- 5 partitions carry fallback counters, all keyed `fallback:fpe_domain_too_small`. None has `fallback:fallback:`.
- 5 evidence packs carry the reason `fpe_domain_too_small`, without a prefix.
- No evidence pack in the store contains `fallback:fallback`.

## Open issues (non-blocking, for F4 polish)

1. **Old evidence packs keep the prefixed label (cosmetic).** 17 old partitions (pre-fix jobs) still hold `fallback:fallback:` counters. 14 of 18 older evidence packs keep a reason labelled `fallback:fpe_domain_too_small`.
   - The packs are immutable and hashed, so this is expected.
   - The console strips the prefix when it renders verification fallbacks.
   - The printable HTML of those old packs may still show the prefixed reason.
2. **Step 5 refusal text is not plain language.** For a bad offset the reason is the raw pydantic message ("target.pk_strategy.offset: Value error, …"). It carries no API code and no plain-language explanation.
   - Step 4 accepts `0` or `abc` in "Key offset" without inline validation; only step 5 catches it.
   - Suggestion: validate the offset in step 4, or map the 422 to plain words.
3. **Offset collisions are found only at prepare.** An offset that collides with existing keys passes `/jobs/run/validate`; the collision pre-check runs at prepare. This is documented in the UI help text ("The collision pre-check runs when the job prepares"). Recording it so nobody reads "all checks passed" as "no collision".
4. **A-N1 confirmed.** `tools/demo_m2.py` fails with a stale `var/demo/<group>.json` after the metadata DB is reset. It should fall back to a new suffix when the group returns 404.
5. **State left on the kept volumes:**
   - `bank_restore.customers` is now masked in place, as `demo_m2` already does to `bank_restore.contacts`.
   - There are `h2-*` probe connections in tenants `default` and `tenantb`, including connections pointing at the invalid host `h2moved.invalid`.
   - Both are removed by the planned `down -v` before release.
