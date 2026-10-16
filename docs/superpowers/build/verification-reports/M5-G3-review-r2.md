# M5 G3 review, round 2 (Tasks 9 to 14)

- **Reviewer:** independent review, round 2. I did not rely on `M5-G3-impl.md` or `M5-G3-fix-r1.md`; every claim below was re-checked by reading code or running it.
- **Working copy:** `/Users/neelu/document_intelligence/dps-wt-m5`, branch `m5`, HEAD `3fa5db6`, clean tree.
- **Commits in scope:** `f2c3ad2` (T9), `f32caed` (T10), `9da77d0` (T11), `23ddb0b` (T12), `5906e0f` (T13), `6392800` (T14), and the r1 fix `3fa5db6`.
- **Fix commit hygiene:** author `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailer, conventional message. It touches 3 paths only: `dps/layers/reports/pdf.py`, `tests/test_evidence_pdf.py` and `M5-DEVIATIONS.md`.
- **Probes:** `/Users/neelu/.cache/dps-build/reports/probe-m5-G3/r2/`. I modified no repository file.

## Verdict: PASS

- The round-1 blocker B1 (an evidence PDF of 58 or more numeric columns gave 500) is fixed and covered by tests. My r1 probes now report no failing column count.
- Every gate is green.
- I found no new blocking defect.
- One new non-blocking defect is of the same class as B1 (N10 below), but its input is far less realistic. The owner should fix it before release.

## 1. Gates (run by me through `./lane.sh`, against this lane's `dps-db`, `source-pg` and `minio`)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` | 290 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success: no issues found in 121 source files |
| `pytest -q -rs` (full suite, including `db` integration) | **1832 passed, 12 skipped**, 1 warning (httpx deprecation), 204 s. Log: `r2/pytest-full.log` |
| G3 suites with `-s`: `test_auth_db`, `test_keys_cli`, `test_tenants`, `test_quota`, `test_admin_api`, `test_reports`, `test_retention_m5`, `test_evidence_pdf`, `tests/db/test_retention.py`, `test_rls.py`, `test_migrations.py` | **115 passed**. Log: `r2/g3-tests-s.log` |
| Frontend | Not applicable: no frontend file changed in T9 to T14 or in the fix |

Notes:
- The full suite gained 12 tests since r1 (1820 → 1832). These are the fix's new tests.
- The 12 skips are the same as in r1, and none is a G3 test:
  - console (3): no app container;
  - OCR mock (3) and tesseract (2);
  - `mostlyai` (2): the `synth-ml` extra needs torch and cannot be installed here;
  - presidio (1) and spaCy (1).
- `seed-sources` needs the lane image, which does not exist. The tests seed their own data, so I started `dps-db`, `source-pg`, `minio` and `minio-init` with `--no-deps`.
- Afterwards I ran `docker compose --profile sources down` from `dps-wt-m5`. It removed only this lane's containers and network.

**Log scan** (`r2/g3-tests-s.log`, app logs on stdout):

| Pattern | Hits |
|---|---|
| Raw key `dps_[A-Za-z0-9_-]{30,}` (real keys are `dps_` + 43 chars) | 0 |
| Any `dps_` followed by 8 or more key characters | 0 |
| 64-hex strings (key or pack hashes) | 0 |
| Value sentinels (`123-45-6789`, `Zebulon`, `Quixote`) | 0 |

## 2. Round-1 blocker B1: verified fixed

`_corr` in `dps/layers/reports/pdf.py`:
- Up to `_CORR_MATRIX_MAX = 16` columns it prints the lower triangle, with value columns clamped to at least 8 mm. 15 × 8 mm + 40 mm = 160 mm, which fits the 180 mm frame.
- Above 16 columns it prints instead:
  - one summary line with the column count, the mean delta (`corr_delta`) and the largest delta (`corr_max`, which `metrics.py:384` produces; if it is absent, the computed maximum is used);
  - a table of the 10 largest pairs, with names and numbers only.
- Cells that are not finite numbers, and rows that are not lists, are skipped.
- Linked packs go through the same function once per table.

Evidence that the fix works:
- r1 probe `p8_isolate.py`: every case renders, including 20, 30, 40 and 60 columns and multi-word names.
- r1 probe `p8b_threshold.py`: "first failing numeric-column count: None".
- The new tests check real content, not only that a PDF is produced:
  - the text "matrix of 80 columns" is present;
  - the planted largest delta (0.731) is present;
  - both column names of that pair are present;
  - the linked pack shows the summary twice;
  - a DB route test of an 80-column pack answers 200 with a PDF.
- Privacy: the new output carries column paths and numbers only, as the matrix did before.

**Deviation (16 columns rather than the "about 20" r1 suggested):** accepted. The arithmetic in the deviation log is correct.

## 3. Re-verification of the G3 surface

Code in T9 to T13 has not changed since r1 (`git diff 6392800 3fa5db6 --stat` lists only the three files above). I re-read the code and re-ran the suites rather than relying on r1.

**T9 keys** (`dps/auth_db.py`, `dps/keys_cli.py`, `042`):
- Only hashes and 8-character prefixes are stored.
- Expiry is checked on every lookup, not only at refresh.
- Rotation:
  - runs in one transaction under `FOR UPDATE`;
  - the unique `rotated_from` index plus a 409 stops double rotation;
  - grace 0 disables the old key at once;
  - a rotated or expired key cannot be re-enabled (409).
- A stale snapshot (10 failed refreshes) fails closed for DB keys only.
- `check_grant`:
  - a wildcard (`*`) is granted only by a wildcard holder;
  - a scope or tenant is granted only if the caller holds it;
  - `_`-prefixed and malformed tenant ids are refused;
  - rotate and enable need the key's scopes and tenants; disable needs its tenants.
- The CLI prints the key once, on stdout. Its logs go to stderr at WARNING level.

**T10 tenants and quotas:**
- `checkpoint` adds to the usage row in the same fenced transaction as the partition update. A fenced-out call counts nothing, so there is no double count.
- The claim CTE leaves an over-quota tenant's partitions queued.
- `POST /jobs/run` checks the quota before admission, except for an idempotent replay.
- `/jobs/run` is the only way to submit a run (`create_job` callers: `submit.py`, plus `retention.py` for `_system`).
- The tenant directory is a `SECURITY DEFINER` function that returns ids only.
- `ALLOWED_EXTRA_POLICIES` lists exactly `owner_directory` and `worker_usage`.

**T11 admin API:**
- Every route needs `admin`, the rate limit and the database.
- Create and rotate answer with `Cache-Control: no-store`.
- Audit search:
  - every value is a bind parameter;
  - `like_escape` is applied to `action` and `q`;
  - each tenant is queried under its own GUC;
  - keyset paging merged across tenants by `(ts, id)` is correct;
  - `tenants=*` is filtered by the caller's grants;
  - `_system` is readable only by wildcard keys.
- `/admin/retention/run` needs a key granted every tenant.

**T12 roll-ups:**
- Statements run under the tenant GUC inside one transaction with an advisory lock.
- Count keys are regex-guarded before any `::bigint` cast.
- Evidence summaries leave out `reveal_map`, `partitions` and `columns`.
- Checked against M4's names in the contract and in the `dps-wt-m4` code (read only): `dps_scan_page.risk_flags jsonb`, `fill.personas` (an integer, 1 to 50) and the `blocked_requests` dict. They match.

**T13 retention:**
- M2's `tests/db/test_retention.py` is unchanged and green.
- Catching `PostgresError` per tenant follows the plan.
- Only one scheduler exists.
- The `retention.run` audit event goes to `_system`.

**T14 PDF:**
- Every `Paragraph` text is `escape(fold(...))`. The test checks there is no `/URI` and no `/Annots`.
- The canvas footer is Latin-1 folded only, which is correct because no markup parser runs there.

**New r2 probe R2-race** (`r2/test_probe_r2.py`): 4 concurrent first requests for one evidence PDF.
- Result: all 4 got 200 with identical bytes; 1 stored `evidence_pdf` artefact; the link is set; a later request returned the same bytes.
- The losing renderers clean up after themselves as the code intends.

## 4. Deviation-log judgements (`M5-DEVIATIONS.md`, Tasks 9 to 14 and the r1 fix)

| Entry | Judgement |
|---|---|
| T9: `_validate` refuses `_`-prefixed and malformed tenants; `covers()`; `check_grant` runs before the state checks; the refresher's pool and exception handling; `schema_ident` | Accept. All strengthen the plan. |
| T9: test fixtures avoid `migrated_db` across event loops; CLI tests use `cwd=tmp_path`; migration-file pin split by range | Accept |
| T10: `registered_hosts` added `IF NOT EXISTS` in 040 | Accept. Carry out the integration note: make M4's 031 use `IF NOT EXISTS` too, or recreate lane databases. |
| T10: `owner_directory TO CURRENT_USER` | Accept, with r1's N1 still open. The posture check's `partition_leaks` refuses staging/prod boot when the app login is the owner; I confirmed this in `db.py:405-409`. |
| T10: uncached quota read; `checkpoint` in a transaction with `RETURNING tenant_id`; no quota check on a replay | Accept |
| T11: one audit path (`audit_action`); refresh after create and enable; 422 `invalid_grace` instead of a silent clamp; per-tenant `/admin/queue` instead of the worker-pool `queue_stats()`; `tenants=*` = directory + grants | Accept. The `/admin/queue` change avoids a cross-tenant pool in the API. |
| T12: no duplicate index; `:`-keys excluded (fixes double counting); `::numeric::bigint`; schema-scoped lock key; zero-filled sweeps; caps of 100; `sha256` computed without `generated_at` | Accept |
| T12: the roll-up document's `entities` are the whole day's API entities, not per action | Accept, as documented. The PDF heading says "(the day's API calls)", so it does not mislead. |
| T13: M5 counter keys only when non-zero; `on_tenant` fencing; `tenant_directory(worker=True)`; `/admin/retention/run` limited to wildcard keys; scan-cascade check conditional until M4 merges | Accept. Re-run the scan-cascade test after the M4 merge. |
| T14: `_num()` formatting; linked/DP rendering; footer folded but not escaped; fixed regulatory text; `evidence_pdf_router` | Accept |
| G3 r1 fix B1 | Accept (section 2) |

## 5. Non-blocking findings

**N10 (new, same class as B1, but rare input): one PDF table cell taller than a page gives 500.**
- **Where:** `_verification` joins every `verification.review_columns` entry into one `_kv` cell. reportlab cannot split a single table row, so `doc.build` raises `LayoutError`, and `GET /evidence/{id}/pdf` answers 500 `internal_error`. The error is value-free.
- **Probes:**
  - `r2/p10_long_cells.py`: with 35-character paths, 100 review columns render and 200 raise.
  - `r2/test_probe_r2.py::test_r2_review_columns_wide_pack_over_route`: 250 columns through the route gives 500.
  - The same failure happens for 400 `consistency.key_custody` entries or 400 `verification.fallbacks` keys. Today's producers emit only a handful of either (`fallback:<note>` has 4 notes), so neither is reachable now.
- **Why it is not blocking:**
  - `review_columns` lists only kept columns that are unclassified *and* that the detector flags at MEDIUM or above, in non-numeric columns.
  - Roughly 130 to 600 such columns are needed, depending on name length.
  - That is not an ordinary table, unlike B1's 60 numeric columns.
- **Fix:** cap list cells, for example to the first 20 entries plus "and N more (see the JSON pack)". Add a render test with 1000 review columns and 1000 custody and fallback entries.

**Carried from r1 (re-checked; code unchanged; still non-blocking owner decisions or hardening):**
- **N1:** with `DPS_PG_MIGRATION_USER` unset, `owner_directory` targets the app login. The posture check refuses staging/prod; `local` only warns. Recommendation: also fail the posture check when `current_user` is in an `owner_directory` policy's roles.
- **N2:** roll-up and usage rows of tenants outside the directory (Layer-1-only tenants) never age out.
  - HANDOFF §4 says they do.
  - New in r2: `GET /reports/api-rollups/{day}` with a future day (for example `2999-01-01`) writes a `dps_report_refresh` marker that never ages out either, one row per request.
  - Fix: age out cross-tenant per table on the worker pool, and refuse days after today.
- **N3:** a key's lifetime is not capped by its creator's.
  - A key inside its rotation grace window can still mint wildcard keys.
  - Recommendation: cap `expires_at` at the creator's expiry; refuse admin mutations by a rotated key; document `grace_hours=0` for compromise.
- **N4:** a tenant-scoped admin can lift its own quota and caps.
  - New in r2: it can also shorten its tenant's `retention_days` to 1. That re-stamps the expiry of every finished job and evidence pack, so the next retention run deletes them.
  - Owner decision: require a wildcard-tenant key for the quota, cap and retention fields.
- **N5:** create and rotate commit before the audit write. A strict-sink 503 after a commit loses the raw key just minted, and with `grace_hours=0` the old key is already disabled. The CLI has the same ordering. It is recoverable by rotating again.
- **N6:** CSV guard gaps: a leading space, a leading newline, or a full-width `＝`. No audit column the guard covers is attacker-controlled today.
- **N7:** test gaps:
  - `/admin/retention/run` is not in the 403/401 route walk;
  - the scan-cascade part of the retention test is skipped on this lane.
  - (The wide-matrix PDF gap is closed.)
- **N8:** `run_retention` drops a tenant's counts when a later step (`_age_out` or `_refresh_recent`) raises after `_sweep` already deleted rows. This affects reporting only.
- **N9:** `/reports/summary` and `/reports/jobs` answer to a key with only `detect`, and expose L2 job ids, object names and failed-sweep paths. These are value-free. Owner decision.

**Nits:**
- Concurrent `ensure_fresh` calls: the request that loses the advisory lock reads without refreshing, so a first page load can briefly show empty days.
- The PDF test `_seed_pack` always uses evidence id `ev_pdf`. This is harmless.

## 6. Probe index (`/Users/neelu/.cache/dps-build/reports/probe-m5-G3/`)

- **`r2/static.log`, `r2/pytest-full.log`, `r2/g3-tests-s.log`:** the gates and the log scan.
- **`r2/p10_long_cells.py`:** N10, renderer only. Run with `./lane.sh python <file>`.
- **`r2/test_probe_r2.py`:** R2-race and the N10 route check. Run with `./lane.sh pytest -c pyproject.toml --rootdir=. -p tests.conftest -s <file>`, with the lane DB up.
- **`p8_isolate.py`, `p8b_threshold.py` (r1):** re-run for B1; all ok, no failing threshold.
