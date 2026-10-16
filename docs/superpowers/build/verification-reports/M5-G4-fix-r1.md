# M5 G4 fix, round 1: review finding B1 (posture report), branch `m5`, worktree `dps-wt-m5`

Commit: `791504a fix(m5): scope the posture report to the caller's tenants and list policy-only tenants in the directory`
(author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailers). Working tree clean afterwards.
Resume check: the worktree was clean at HEAD `86b1920` and there was no earlier fix report, so nothing was half-done.

## B1(a): the posture report showed other tenants' names (fixed)

- `dps/posture.py`:
  - `posture_report(settings, *, environ=None, principal=None)` takes the caller as a new keyword argument.
  - `_tenant_warn_policies(principal)` skips directory tenants the principal cannot `can_access`.
  - `_stale_keys(settings, principal)` now also selects `tenant_ids`. It keeps only keys the principal
    `covers()`, the same rule as `GET /admin/keys`, so a non-wildcard admin does not see a `*` key either.
  - `_db_checks` passes the principal through.
  - `principal=None` (internal callers) and a wildcard key keep the full platform view.
  - `Principal` is imported under `TYPE_CHECKING` only.
- `dps/layers/api/routers/admin.py`: `/admin/posture` passes `principal_of(request)`.
- Consequence, by design and documented: a single-tenant admin's `leakage_strict` (tenant-policy part)
  and `keys_rotated` are judged on that admin's tenants only. Checks that cover the whole deployment
  (settings, RLS, migrations) are unchanged.
- The report shape is unchanged, so the m5ui `PostureReport` type still holds.

## B1(b): `leakage_strict` missed policy-only tenants (fixed)

- New migration `dps/migrations/045_policy_directory.sql` (inside M5's 040–059 range):
  - an owner SELECT-only policy `owner_directory` on `dps_policy`, needed because FORCE RLS also applies to the owner;
  - `CREATE OR REPLACE dps_tenant_directory()`, which adds `UNION SELECT tenant_id FROM dps_policy`.
    It still returns ids only, keeps SECURITY DEFINER and the pinned search_path, and re-asserts REVOKE PUBLIC / GRANT dps_app_rw.
- `dps/db.py`: `ALLOWED_EXTRA_POLICIES["dps_policy"] = {"owner_directory"}`, so `assert_rls_posture` accepts the new policy.
- `dps/tenants.py`: docstrings updated.
- Side effect: every directory reader also sees policy-only tenants. That covers the admin tenant list and the `*` audit expansion, retention, and rekey. This is correct for all of them.

## Tests (both new tests failed before the fix, for the reported reasons)

New tests in `tests/test_posture_m5.py`:
- `test_a_policy_only_tenants_warn_version_fails_leakage_strict` (db).
  - Before the fix: `assert 'p-only' in []`.
  - After the fix: the tenant is in the directory, `leakage_strict` is `ok=False`, and the detail names `p-only/retail`.
- `test_posture_shows_a_single_tenant_admin_only_its_tenants` (HTTP, `api_client`).
  - Setup: t1 and t2 each have a warn policy and a 200-day-old DB key, and there is a t1-only admin key.
  - Before the fix: the scoped body contained `t2-ledger-recon is 200 days old`.
  - After the fix, for the t1-only admin:
    - the body contains no `t2-` or `t2/`;
    - `keys_rotated` reads exactly `t1-ledger-recon is 200 days old`;
    - `leakage_strict` names `t1/t1-merger-project`.
  - After the fix, the wildcard key sees both tenants' names.
  - Neither response body contains the raw key.

Other test changes:
- `tests/test_tenants.py`: the directory test adds a policy-only tenant `d`, and the RLS-posture test asserts the `dps_policy` owner policy.
- `tests/db/test_migrations.py`: `M5_FILES` lists 045.

Reviewer probes, re-run (`probe-m5-G4/test_probe_g4.py -k "p1 or p3 or p5"`, 3 passed):
- p1: no t2 policy name and no t2 key name.
- p3: `posture leakage_strict: False tenant policy t1/gdpr-pseudonymise must use verify.leakage_check=strict`. The request-time refusals are unchanged (422 `verify_warn_disallowed` everywhere).
- p5: the t1 admin does not see the t2 policy.

## Gates

| Gate | Result |
|---|---|
| `./lane.sh ruff format dps tests tools` | 301 files left unchanged |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 126 source files |
| `./lane.sh pytest -q` (only `dps-db` up) | 1828 passed, 83 skipped (was 1826 + the 2 new tests) |
| `./lane.sh pytest -q -rs` with `dps-db` + `--profile sources` (image `data-privacy-services:m5` rebuilt with 045; app and worker healthy, so `migrations_verified` holds) | **1899 passed, 12 skipped** |
| `docker compose exec worker python -m dps.rekey --dry-run` | `{"already_current": 0, "resealed": 0, "rows": 0, "unreadable": 0}`, exit 0 |
| Frontend | not touched, so the npm gates were not needed |
| `docker compose --profile sources down` | done; 0 lane containers left |

The 12 skips have the same causes as in the review, and none are G4 paths:
- the `mostlyai` and presidio/`[ner]` extras are not installed;
- 3 tests need azure-ocr-mock (docs profile);
- the tesseract binary is not on PATH;
- 3 console tests need the demo connections from `tools/smoke_test.py`.

## Docs and deviations

- `docs/superpowers/build/M5-DEVIATIONS.md`:
  - new section "G4 review r1 fix — B1";
  - review N6 added under "Deferred to final stage": the PrometheusRule moves to Task 25 / WP-F2.
- Contract (`2026-10-11-m1-m5-contract.md`):
  - §1: M5 migrations now run to 045;
  - §2.1: `ALLOWED_EXTRA_POLICIES` comment;
  - §2.2: the 045 entry;
  - §9: posture scope (tenant policies and keys cover only the caller's tenants; a wildcard sees all).
- `HANDOFF.md` §5 runtime posture: the scope note, and to use a `*` admin key for the platform-wide view.

## Open issues (not blocking; not addressed in this round)

- The review's non-blocking nits N1–N5 and N7–N9 are untouched:
  - N1: a 500 on an out-of-range version or `from`;
  - N2: no optimistic draft check at publish;
  - N3: the oracle tcps check is a substring test (M3 / WP-I);
  - N4: a `.env` file is not covered by the files-only secret rule;
  - N5: no TRUNCATE leg on the immutability trigger;
  - N7: a placeholder-shaped value can hide from the verifier;
  - N8 and N9: by design.
- Privacy note: no value-bearing output was added. The new details carry only tenant ids, policy names and key names that the caller is entitled to see.
