# S3-impl — fsec: M5 Task 0, Task 17 and every confirmed security finding

Worktree `/Users/neelu/document_intelligence/dps-wt-fsec`, branch `fsec` (base main `64c46e3`),
head **`4d2b234`**, pushed by `tools/dev/autopush.sh`. Date 2026-10-14. Status: **DONE**. One item
can only land with the M4 merge: the F-03 wiring (see the last section).

## Commits (18; all authored `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailers)

| Commit | Content |
|---|---|
| `dc85450` | Task 0: `tests/test_contract_surface.py`. `validate_run` is listed where it moved to (`dps.layers.connectors.targets`). The M4 names (`dps.layers.scanner.scan_job`, `/api/v1/scans`) sit in a **strict-xfail** test: it fails once M4 lands, which forces the merge to move them into `SURFACE`. The route walk uses `iter_route_contexts` |
| `6061767` | Task 17, under the plan's commit message:<br>• S1 scrubbed 422s (`dps/layers/api/validation.py`, helpers in `dps/errors.py`, validators that no longer echo values)<br>• S2 secret reprs (13 settings, plus the engine's binding keys and token caches)<br>• S3 ReDoS guard (`regex_guard.py` wired into `PatternSpecModel`; a `**` glob collapses to `*`)<br>• S5 `check_scan_url`, `chromium_resolver_rules` and `forbidden_ip_reason` (functions and tests)<br>• S6 `__dps_scope__` marker and the route walk<br>• S11 `Cache-Control: no-store` and `Pragma: no-cache` on `/api/`<br>• S13 `tests/test_no_values_in_logs_m5.py`<br>• F-09: the root logger never goes below WARNING; Presidio and spaCy loggers capped<br>Also `tests/security/*`, the review doc and the contract |
| `1fd9730` | S1 test correction: inline policies now use the `preset` key, so the test cases actually reach the value validators |
| `f112044` | F-01 (D3): a `secret_ref` held by a production-tagged connection counts as a production credential. Create and patch refuse it without admin; `validate_run` takes `production_refs=`; a retry re-checks admin through `targets.check_retry_scope` |
| `54b5bb4` | F-06: the RLS posture names any tenant-table policy that is open to the runtime login (`db.CROSS_TENANT_PROBLEM`). The app now refuses to boot on it in every environment. HANDOFF updated |
| `6c4a3ef` | F-07: formula guard on CSV `files` exports (`_csv_cell`). Phone-number and plain-number shapes are left untouched |
| `514280d` | F-08: SSRF guard on connections (`netguard.check_connection_endpoint`) at create, patch and use. It covers the host, the S3 `endpoint_url`, the Mongo URI and the Oracle DSN; refusals are 422 `host_forbidden` |
| `ceff56c` | F-04: an NER hit yields to an overlapping typed hit of a higher sensitivity tier. A date of birth is no longer downgraded to DATE and kept in clear |
| `c24c96f` | F-10 and F-12, in one commit:<br>• F-10: custom entity types appear in metrics only as `custom`; the custom registry does not store MEDIUM entries, is capped at 5000 and fails closed when full; policies are capped at 50 recognizers, 20 patterns, 1000 deny/allow terms and 500 declared fields<br>• F-12: synthesize methods must be key-less, enforced by a policy validator and by `build_plan` (422 `synthesize_method_invalid`) |
| `e89ece8` | F-11: `DPS_TOKENIZER_PROFILES`, a per-tenant allow-list checked before any tokenizer call. Refusals are 403 on L1 and job code `tokenizer_profile_not_allowed` on L2. Each batch is audited as `external.tokenize`. The staging/prod posture requires the setting. HANDOFF updated |
| `6ec3933` | F-19: every external token cache is keyed by `(entity, profile, value or token)` |
| `4e92dd2` | F-13: the key check value is stored in the job spec and in `dps_job_group.kcv` (migration **046**). Retry and group admission now compare the DEK, not only the kid |
| `fde5a94` | F-14: if the creation audit fails after commit, the job or group is removed. An unclaimed job is deleted together with its key and Idempotency-Key; a claimed one is canceled |
| `7c3fd5c` | F-18: policy bodies are sealed in `spec_enc` (`SealedSelection.policy`); job rows keep only `policy_label` |
| `da904a9` | F-20: synthesis seeds are wrapped with `wrap_key` on L1 JSON, NDJSON and documents, on `jobs/run` and on `job-groups`. `GroupRequest` now uses `extra="forbid"` |
| `d2bada1` | F-21: the previous KEK is tried when opening sealed data (registered through `load_kek`; `dps.rekey` stays strict). Idempotency fingerprints made under the previous KEK still match |
| `0c63e01` | F-22: bind tag v2 (JSON `[entity, profile]`, with a `bind_v` field); v1 tags still verify. External reveal hints must name a real entity type |
| `4d2b234` | Review doc: map of commits to fixes, probe re-runs, residual risks |

Each confirmed finding, F-01 to F-22, has a test that fails without its fix. Each test was written
first, seen failing, then made to pass. `docs/security/2026-10-11-m5-security-review.md` lists S1–S13
and F-01 to F-22 (fix, test, status), the 3 refuted candidates and the residual risks. Deviations from
the plan are in `docs/superpowers/build/M5-DEVIATIONS.md`, sections "Task 0", "Task 17" and "Security
review fixes F-01…F-22", all marked as finish-stage work.

## Gates (on `0c63e01`, which contains all the code changes)

- `ruff format --check`, `ruff check dps tests tools` and `mypy dps`: clean (136 files).
- `pytest -m "not integration"` with the lane DB up: **2620 passed**, 10 skipped, 1 xfailed (the M4 surface).
- `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "integration and not browser and not ocr and not slow"` on the
  fsec stack (dps-db, source-pg, MinIO, MongoDB, Oracle Free): **237 passed**, 1 skipped.
- `-m slow` (resume-after-kill, benchmarks): **12 passed**.
- Copula and surrogate jobs on the seeded demo bank: 7 passed.

## Proofs: the finders' probes re-run on the fixed branch

Outputs are in `S3-impl/probes/*.after.out`.

- **D3 clone verifier:** creating the clone gets 403 `admin_scope_required`; 0 rows of the production table changed.
- **ReDoS:** `(a+)+$` is refused before it runs.
- **Tokenization oracle:** with a production-style allow-list, `/obfuscate` answers 403 twice, the tokenizer is called **0** times, and inversion fails. Tokenization is unrestricted only when `DPS_TOKENIZER_PROFILES` is unset, which the prod posture refuses.
- **`/synthesize` with an `external_tokenize` override:** 400, and no bank token is returned.
- **Profile cache:** 20 of 20 tokens use each column's own profile.
- **S11:** every answer that carries a key is `no-store`.
- **Settings repr:** `repr(Settings)` shows 0 of 13 secrets.
- **Metrics:** requests with 1000 recognizers add 0 metric series and 0 taxonomy entries; no custom name appears on `/metrics`.
- **KEK rotation:** a blob sealed under the previous KEK opens during the rotation window.
- **Bind tag:** a forged `ACCT|p` hint is refused.
- **Real Presidio (p06):** the date of birth is in clear in 0 of 8 runs (before the fix: 3 of 4 with NER on).

## For the orchestrator and the next phases

1. **M4 merge (S3 phase 2 / S1):**
   - Wire `netguard.check_scan_url` into `POST /scans` (422 `host_forbidden` or `host_not_registered`), and pass `--host-resolver-rules=<chromium_resolver_rules(hosts)>` in the crawler's launch flags. This is F-03 and blocks the merge.
   - In `tests/test_contract_surface.py`, move `M4_SURFACE` and `M4_ROUTES` into `SURFACE` and `ROUTES`; the strict xfail fails until that is done.
   - The scans route's `find_by_idempotency` and `create_job` calls should pass `alternates` / `idempotency_alternates` (F-21).
   - `tests/security/test_route_auth.py` will also walk the scans routes.
2. **Deploy streams (fimg/fos):**
   - The staging/prod posture now requires `DPS_TOKENIZER_PROFILES` whenever `DPS_TOKENIZER_PROVIDER=http`; add it to the prod overlays and configmaps.
   - The app refuses to boot if migrations ran as the runtime login, so always set `DPS_PG_MIGRATION_USER`.
   - Migration `046_job_group_kcv.sql` must be applied (the `migrate` one-shot).
   - The local compose stack is unaffected.
3. **Pre-existing flaky test (not caused by this work):** `tests/test_ndjson.py::test_one_audit_event_per_stream`
   fails about 1% of the time. NDJSON obfuscation of two-letter email local parts sometimes maps one
   record's address onto another's, which trips the cross-record leakage sweep. Measured: 4 of 400 on
   the base `64c46e3` code, 5 of 400 on fsec. Fix the test data (longer local parts) or the
   small-domain email method later.
4. **Lane state:**
   - `dps-db` and `source-pg` are up on the `dps-wt-fsec` stack; the demo bank is seeded on port 5484.
   - MinIO, MongoDB and Oracle were created for the integration run and are now **stopped**. Restart them with `docker compose --profile sources --profile oracle start minio mongo oracle`.
   - No fsec image was built.
