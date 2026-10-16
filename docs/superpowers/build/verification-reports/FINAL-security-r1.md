# FINAL — v1.0.0 candidate on `main` — SECURITY lens verifier, round 1

**Verdict: PASS. No blocking defect in the security lens.** Every item of the lens holds on the release tree and on the live stack:
- the security guard suites are green: **509 + 104 passed, 0 failed, 0 skipped**, with `DPS_TEST_REQUIRE_SERVICES=1`;
- SEC-2, SEC-3, SEC-4 and SEC-5 hold when probed live;
- the route-auth walk: **86/86 operations** refuse as designed, on the live server;
- cross-tenant access answers **404, the same as a missing id**, on 21 read routes and 10 mutation routes;
- the ReDoS bound holds on the real uvicorn server, and `/health` never stalled (**0 of 1,884 liveness checks over 0.5 s**) during back-to-back `/synthesize` and `/documents/synthesize` attacks and with both matchers saturated;
- deny-list builds are charged and bounded;
- external tokens are bound to the caller's key: the bank's detokenizer was **called 0 times** across 11 forged or cross-key reveals;
- the posture guard refuses **29/29** unsafe production settings and **12/12** literal secrets, and the shipped images refuse to boot.

Two non-blocking items need the owner (§9): the deny-list build ratio against the round-1 acceptance, and a documentation nit. The gates verifier's B1 is caused by fsec's F-18 seal. This report confirms on the live DB that the seal holds, and states the one constraint its fix must respect (§9 X1).

| | |
|---|---|
| Date | 2026-10-16, 09:56–10:16 IST |
| Tree verified | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ **`45be8ac`** (`chore(release): v1.0.0`). HEAD unchanged. **No repository file was modified by this verifier.** `git status` shows one untracked directory, `docs/screenshots/v1.0.0/`. It was created at 10:10 IST by the console verifier's `FINAL-console-r1/curate_v1.py`, not by me. |
| Stack | The `full` stack left running by the release step and the gates verifier. It was not restarted, rebuilt or brought down. All 11 services stayed healthy, and `/readyz` reported `ready: true` (version 1.0.0) at the end. |
| Endpoints | Contract §12.2 defaults: app 8400, dps-db 5441, source-pg 5442, MinIO 9010, Oracle 1522, Mongo 27018, OCR 5016, demo site 8410, tokenizer-mock 8420. The env file was copied from the gates run: `FINAL-security-r1/env.sh`. |
| Work dir | `~/.cache/dps-build/reports/FINAL-security-r1/`: probes (`probe_*.py`, `common.py`, the two copied S3c matrices), `results/*.json` (value-free check rows), `logs/` (pytest logs and junit XML, one log per probe, matrix outputs) |
| Values | Every probe records only statuses, codes, counts, timings and booleans. Every SSN, password, term and pattern used is synthetic. The API keys were minted for the probe's own tenants, held in process memory only, and disabled at the end of each probe (all **37 disabled**). The tokenizer-mock bearer token and the bootstrap key are the stack's documented local literals and were never printed. |
| Footprint on the shared stack | Tenants `fsr1-a` and `fsr1-b` only. All of the probe's connections were deleted (0 left). Three discover jobs are left in a terminal state (2 failed, 1 canceled). `connections.py` auto-created one bare `dps_tenant` row (`fsr1-a`, all overrides NULL) the first time a connection was stored. Objects of the `default` tenant were only **read**, with a foreign key. No connection, scan or job was created in `default` or `retail-banking-apac` (every attempt there was meant to be refused, and was). |

## 1. Security guard suites (`run_pytest_security.sh`, `logs/pytest-security*.{log,xml}`)

Run 1: **509 passed, 0 failed, 0 skipped**, 63.5 s, with `DPS_TEST_REQUIRE_SERVICES=1` against the stack's dps-db. It covers:
- `tests/security`: **302 tests in 22 files**: binding tag v2, connection SSRF, cross-tenant walk, custom-entity bounds, event-loop freeze, external profile cache, headers, KEK rotation window, key binding, netguard, pattern budget load, pattern matcher, regex guard, route auth, scan SSRF, scanner paths, secrets hygiene, seed wrap, selection injection, synthesize keyless, tokenizer profiles, validation errors;
- the review's other guard tests:
  - `test_no_values_in_logs_m5` (64), `test_contract_surface` (4);
  - `test_posture`, `test_posture_m2`, `test_posture_m5` (21 + 10 + 27);
  - `test_rls_posture_unit` (3), `tests/db/test_rls` (25), `test_llm_egress` (14);
  - `tests/api/test_production_resource_gate` and `test_production_write_gate` (the joined D3 rule of the release merge), `test_inline_policy_sealed` (F-18), `test_once_only_key_custody` (F-14), `test_connection_ssrf_api`;
  - `tests/scanner/test_scan_pattern_budget`, `test_browser_worker_posture` (18), `test_scan_ssrf_browser` (real Chromium).

Run 2: **104 passed, 0 failed, 0 skipped**, 14 s. These are the remaining guard tests the security review names outside `tests/security`:
- `test_external_binding` and `test_external_tokenize` (SEC-4, against a counting mock);
- `test_auth_db` (S8, no privilege escalation);
- `test_admin_api` (S9 CSV);
- `test_evidence_pdf` (S10);
- `test_observability` (S13, third-party loggers capped);
- `test_ner_dob_precedence` (F-04);
- `tests/connectors/test_files` (F-07, CSV formula guard).

## 2. Route-auth walk, live (`probe_route_auth.py` → `results/route_auth.json`, 12/12)

The routes come from `iter_route_contexts(create_app(...))`, the same way `test_route_auth` walks them. That gives **86 operations** (the 4 L1 routes each have a JSON and an NDJSON variant). The live `/api/v1/openapi.json` lists **82 distinct operations, the same set as the code** (no extra and no missing route). On the live server, every one of the 86 answered as follows:

| Call | Result |
|---|---|
| no key | **401** on 86/86, each with `WWW-Authenticate: ApiKey …` |
| well-formed but unknown `dps_` key | **401** on 86/86 |
| a key holding every scope **except** the route's declared one(s) (8 such keys) | **403** on 86/86 |
| a key holding all scopes, with `X-DPS-Tenant` naming a tenant it does not hold | **403** on 86/86 |

- Every route declares a scope; no request reached a handler.
- `/health`, `/readyz` and `/metrics` stay keyless by design.

Tenant-header edges (`probe_tenancy_more.py`):
- With the wildcard key, `_system` and `_shared` get 403 `tenant_reserved`.
- `../default`, `*` and a 65-character id get 400.
- A CR/LF header is refused by the client itself.

Key-grant escalation (S8, `probe_extra.py`). A tenant admin of `fsr1-a` gets:
- 403 `tenant_escalation` when it names another tenant or `*`;
- 403 `scope_escalation` when it names a scope it does not hold or `*`;
- 422 `invalid_tenant` for `_system`.

It lists only keys within its own tenants (22 listed, 0 foreign, no hash or key field) and sees only its own tenant row.

## 3. SEC-2 and SEC-3 / F-01, live (`probe_tenancy.py`, `probe_tenancy_extra.py`)

**SEC-2: secret refs are tenant-scoped.** The secret tree holds `default/` and `retail-banking-apac/` (folder names were listed, never contents).

| Case | Result |
|---|---|
| `fsr1-a` creates a connection naming `default/…` or `retail-banking-apac/…` | 403 `secret_ref_foreign_tenant`, ref not echoed |
| `default` creates a connection naming `retail-banking-apac/…` | 403 `secret_ref_foreign_tenant` |
| `../default/…`, `/etc/passwd`, `a/../../b`, `dps/../../default/…`, `.hidden/x`, `dps//x`, `Default/dps` | 422 (`invalid_request` / `secret_ref_invalid`), value-free |
| `_shared/…` that does not list the tenant | 403 `secret_ref_not_shared` |
| **Use:** `fsr1-a` stores the same **relative** ref as default's production login (`dps/src/bank-pg-ro`) | stored (201); `POST …/test` answers `ok: false`, `error_code: secret_unavailable`. The ref resolves only inside `fsr1-a/`, never to default's secret. |
| PATCH onto `default/…` | 403 `secret_ref_foreign_tenant`; the ref is unchanged |

**SEC-3 / F-01: who may change a connection.** All cases ran in `fsr1-a`, with a `jobs`+`scan` key against an admin-created `production` connection.

| Case | Result |
|---|---|
| a `jobs` key creates a `production`-tagged connection | 403 `admin_scope_required` |
| PATCH of the production connection: host, tags, `secret_ref`, credentials, name, roles | 403 `admin_scope_required` ×6 |
| DELETE of it | 403 `admin_scope_required` |
| after these refusals | the connection is unchanged (name, params, ref, tags, roles, `has_secret`) |
| F-01: clone the production credential untagged; re-point another connection onto it | 403 `admin_scope_required` ×2 |
| endpoint change of a non-production connection, no credentials | 403 `connection_endpoint_change_requires_credentials` |
| the same, with the `secret_ref` restated | 200 |
| sealed inline credentials (local posture): endpoint change without them | 403; `md5(secret_enc)` unchanged |
| the same, with new credentials | 200; the old seal is **replaced** (digest differs) |
| switching to a `secret_ref` | 200; `secret_enc` becomes **NULL** |
| audit rows | 26 `connection.update` rows (13 per probe run). The API view and the DB rows carry no host, ref, user or password. The DB `counts` keys are field names only (`field.params.host`, `field.credentials`, `field.secret_ref`, `field.tags`, `field.name`, `field.roles`, …). |

## 4. Cross-tenant 404s, live (`probe_tenancy.py`, `probe_tenancy_extra.py`, `probe_tenancy_more.py`)

**Reads.** Real objects of `default` were read with a key of `fsr1-b`. The objects were a connection, a job, evidence, an artefact, a scan with pages (a scan that another verifier was running), a snapshot, a job group and a stored policy. The read routes:
- connections: `GET`, `/objects`, `/describe`;
- jobs: `GET`, `/partitions`;
- evidence: `GET`, `/html`, `/pdf`;
- `GET /artefacts/{id}`;
- scans: `GET`, `/pages`, `/pages/{n}`, `/events`, `/inventory`, `/export`;
- `GET /snapshots/{id}`, `GET /job-groups/{id}`;
- policies: `GET /policies/{name}`, `/versions/1`, `/diff?from=1&to=draft`.

All **21 routes answer 404, with the same status and code as a missing id of the same shape** (no existence oracle). The owner's own key does not get 404 on them (control).

**Lists.** `fsr1-b`'s lists, reports and stats contain none of default's ids:
- `/connections`, `/jobs`, `/evidence`, `/scans`, `/snapshots`, `/job-groups`, `/policies`;
- `/reports/summary`, `/reports/jobs`, `/reports/evidence`;
- `/jobs/stats`, `/admin/keys`, `/admin/queue`.

`fsr1-b`'s admin gets 403 `tenant_forbidden` for `?tenants=default`, and its own audit view holds 0 foreign rows.

**Mutations.** `fsr1-b` attempted 10 mutations on `fsr1-a`'s connections and job:
- PATCH and DELETE of both connections;
- `…/test` and `…/count`;
- job pause, resume, retry and cancel.

All answered 404, and nothing changed: names, tags and job state as before.

**Idempotency.** `fsr1-b` replayed `fsr1-a`'s discover `Idempotency-Key`:
- `fsr1-b` got 404 `connection_not_found`, no replay header, and not A's job id;
- `fsr1-a`'s own retry is the replay (control).

## 5. SEC-4: external tokens bound to the caller's key, live (`probe_sec4_sec5.py` → 11/11)

"Not called" is proven twice: by the tokenizer-mock's own request log (one line per request, never a body) and by the absence of new `external.detokenize` audit rows for the probe's tenant.

- `/obfuscate` with `SSN: external_tokenize (profile ssn)` produces 2 external hints, each with a 26-character `bind` tag.
- The legitimate `/reveal` round-trips with **1** detokenize batch and **1** audit row.
- A real bank token for someone else's SSN was minted directly at the mock. These attempts were refused with **403 `external_token_not_bound`**, value-free:
  - no tag;
  - a made-up tag;
  - another token's replayed tag;
  - a non-string tag;
  - another profile;
  - another entity type;
  - one forged hint among legitimate ones;
  - the NDJSON twin (the error line).
- **The confused deputy proper:** tenant B re-labelled A's hints with B's own `kid`/`kcv` (so the key checks pass) and asked DPS to detokenize A's tokens, then a bank token minted outside DPS, each under B's key material. Both got **403 `external_token_not_bound`**.
- A naive cross-key reveal (A's hints with B's key material) is refused one step earlier: 400 `bad_request`, "kid mismatch", value-free.
- Over all refused reveals: **mock `POST /v1/detokenize` = 0** and **audit `external.detokenize` rows added = 0**. Every refusal is audited with a non-ok outcome.

## 6. SEC-5: a scan's `storage_state_ref` is tenant-scoped, live (`probe_sec4_sec5.py`)

`POST /scans` (`max_pages` 1) with these refs:

| Ref | Result |
|---|---|
| `default` naming `retail-banking-apac/dps/ui/demo-onboarding` | 403 `secret_ref_foreign_tenant` |
| `retail-banking-apac` naming `default/…` | 403 `secret_ref_foreign_tenant` |
| `../…` | 422 `auth_invalid` |
| an absolute path | 422 `auth_invalid` |
| `_shared/…` | 403 `secret_ref_not_shared` |

No response echoes the ref, and **0 scan jobs** were created by the probe's keys (DB count before and after).

**SSRF (S5 / F-08, `probe_extra.py`, `probe_ssrf_extra.py`).**
- Connection endpoints are refused with 422 `host_forbidden` before they are stored:
  - PostgreSQL hosts: `169.254.169.254`, `0xA9FEA9FE`, `2852039166`, `::ffff:169.254.169.254`;
  - an Oracle host at the metadata IP;
  - S3 `endpoint_url`s at the metadata IP (literal, hex, IPv4-mapped).
  - The MinIO control was accepted.
- Scan start URLs are refused before any job exists: metadata, hex, decimal, IPv4-mapped, the userinfo trick, an unregistered host (`host_forbidden` / `host_not_registered`).
- Registering the address does not help: `check_scan_url` (in-process, with each host *registered*) still refuses `169.254.169.254`, `::ffff:169.254.169.254`, `metadata.google.internal`, `100.100.100.200` and `fd00:ec2::254` with `host_forbidden`.

**Headers (S11).**
- An `/obfuscate` answer carrying key material, and `GET /jobs`, are `Cache-Control: no-store`, with `Pragma: no-cache` on the former.
- Every `/api/` answer carries `nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`.
- Evidence HTML: `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'`, plus `nosniff` and `no-store`.
- Artefacts: `application/json` with `attachment`.

## 7. ReDoS guard on the live server (`probe_redos.py`, `probe_redos_filter.py`)

The setup is the app container's uvicorn process with the defaults: `DPS_PATTERN_CPU_SECONDS` 5 s, 2 matcher processes. The attack is the reviewer's policy: 1000 patterns, one of them stacked (`\d+\d+\d+[x!]`), on 10 KB of digits. A separate connection polled `/health` every 20 ms.

| Phase | Requests | `/health` |
|---|---|---|
| baseline, no load | — | 98 checks, max 0.003 s |
| `/synthesize`, back to back for 14 s | 3/3 **422 `custom_pattern_too_slow`** at 5.17 / 5.02 / 5.03 s | **646 checks, 0 failed, 0 over 0.5 s, max 0.058 s** |
| `/documents/synthesize` (an uploaded text file), back to back for 14 s | 3/3 422 `custom_pattern_too_slow` at 5.02–5.03 s | **612 checks, 0 failed, 0 over 0.5 s, max 0.054 s** |
| 4 `/detect` + 1 `/synthesize` attacks at once (both matchers saturated) | 5/5 422 `custom_pattern_too_slow`; an ordinary `/detect` beside them answered 200 in **0.01 s** | **626 checks, 0 failed, 0 over 0.5 s, max 0.030 s** |
| `/synthesize` with 20 new inline deny lists (1000 × 50 characters, 1.1 MB) | 200 in 2.55 s | 68 checks, max 0.070 s |
| `/synthesize` with the reviewer's 50 new lists (2.7 MB; before R3-B2: 34 CPU-seconds uncharged) | 422 `custom_pattern_too_slow` at **5.39 s**: the build is charged and stopped at the ceiling | 121 checks, max 0.077 s |

The static first filter refuses `(a+)+$` before any run. Every refusal names the rule ("nested quantifiers") and none echoes the pattern:
- `/detect`, `/obfuscate`, `/synthesize`: the engine's 400 `bad_request` "invalid policy" (as S1 documents for inline policies);
- `/policies/validate`: 400;
- `/policies/test-recognizer`: 422 `invalid_recognizer`.

## 8. Deny-list trie build cost (R3-B2, `trie_vs_round1_matrix.py`, `cold_matches_build.py`)

These are the S3c reviewer's two matrices, re-run unchanged on this tree: best of 3, GC paused, caches cleared. They ran on the host (`.venv`, macOS) and **inside the app container** (the shipped Linux image, uid 10001). The ratio is round-3 trie build (text + compile) ÷ round-1 flat compile.

| Shape | Host | Container |
|---|---|---|
| ASCII random 1000 × 8 / 20 / 50 / 140 / 256 | 1.66 / 1.37 / 1.21 / 1.14 / 1.09× | 1.62 / 1.36 / 1.19 / 1.09 / 1.05× |
| names "First Last N"; account ids; e-mails | 1.24; 1.05; 1.17× | 1.21; 1.03; 1.15× |
| Turkish names | 1.87× | 1.86× |
| CJK 1000 × 50 | 1.02× | 1.03× |
| **cold per-character case cache** (first list with unseen characters): CJK × 8, CJK × 50, Latin-ext, Greek+Cyrillic, ASCII × 20 | **2.30×** (26.6 vs 11.6 ms), 1.36, 1.41, 1.21, 1.52× | **2.40×** (29.5 vs 12.3 ms), 1.16, 1.45, 1.24, 1.36× |

Absolute cost is at most about 0.2 s per 1000-term list; the largest is ASCII × 256 at 214 ms. The builds are **charged to the call and bounded by the ceiling**. Two kinds of proof:
- live (§7): 50 new lists were stopped at 5.39 s, with `/health` unaffected;
- tests: `test_a_deny_list_build_is_charged_to_the_call`, `test_a_call_past_its_ceiling_builds_no_further_deny_list` and `test_the_reviewers_2_7_mb_deny_list_policy_answers_within_the_ceiling`, all green.

So no uncharged or unbounded CPU path remains. The ratio against the "≤ round-1" acceptance is the known owner decision (N1).

## 9. Production posture (`probe_posture.py` → 11/11, `probe_docs_prod.py`)

**In-process, the main tree's code.** The run used a clean environment and secrets as mounted `*_FILE`s in a temp dir.
- A safe `prod` configuration passes for `app`, `worker` and `browser-worker`, and `create_app` builds.
- **29/29** unsafe settings are refused, each naming its setting, and `create_app` refuses to build for every one of them. The settings, grouped:
  - auth and keys: auth off; a bootstrap key;
  - LLM: the stub provider; Stellar without a CA; Stellar over http with userinfo; COIN over http; COIN TLS opt-out without the acknowledgement;
  - audit: audit off; audit not strict;
  - scanner: unmasked scan originals; a headed browser;
  - OCR: Azure OCR without a key; Azure OCR over http;
  - database: inline secrets; RLS off; migrations `auto`; no PG TLS root; the embedded worker; no KEK;
  - egress: egress TLS off; the tokenizer over http; S3 over http; no organisation CA;
  - tokenizer: no token; F-11 unrestricted profiles;
  - CORS: `*`; `*` inside a list;
  - verification: verification relaxable; LLM egress check not strict.
- **No message carries a value**: a sentinel embedded in URLs, userinfo, keys and hosts never appeared.
- Each of the **12** secrets given as a literal environment variable is refused with "must be provided as …_FILE".
- The browser-worker keeps its capture rules: unmasked originals are refused, and the API-only rules (CORS, tokenizer profiles) are not applied to it.
- In prod, no Swagger UI, ReDoc or OpenAPI document is served. `/docs` and `/redoc` fall through to the console's 1 KB shell, OpenAPI answers 404, and `app.docs_url`, `redoc_url` and `openapi_url` are all `None`.
- A CORS preflight from an unlisted origin gets no `Access-Control-Allow-Origin`.

**The shipped images** were run with `docker run --rm --network none --read-only`. They were named, and none was left behind.

| Image and entrypoint | Configuration | Result |
|---|---|---|
| `data-privacy-services:local`, uvicorn entrypoint | bare prod with a bootstrap key | rc 1, "posture" |
| `data-privacy-services:local`, `python -m dps.worker` | prod with PG host and KEK | rc 1, "posture" |
| `data-privacy-services-browser:local`, `python -m dps.browser_worker` | unmasked originals | rc 1, "posture" |

None of the three started serving, and none echoed the sentinel or the KEK.

## 10. Other live proofs

- **F-18 on live data**, counts only:
  - every object-typed `spec.policy` in `dps_job` is a sealed label `{name, sealed, sha256, version}` with `spec_enc` present (3 discover jobs and 5 run jobs);
  - every string policy is a preset name (2);
  - **0** job rows hold `deny_list`, `allow_list`, `custom_recognizers`, `patterns` or `treat` in clear.
- **Log scan.** I scanned `docker compose logs --since 04:25Z` for app, worker, browser-worker and tokenizer-mock: 3,898 lines covering this verifier's whole window plus the other verifiers' concurrent activity. The needles were:
  - the probes' synthetic SSNs and probe credentials;
  - the sentinel;
  - the smoke sample's e-mail address and phone number;
  - the bootstrap key literal, the mock bearer token and the `tk_` token prefix;
  - the attack patterns.

  Result: **0 hits**.

## 11. Findings

**Blocking (security lens): none.**

**Non-blocking, for the owner:**
- **N1 (owner decision, already escalated by S3c and the release report).**
  - The acceptance for the trie build was "at or below the round-1 cost". It is met only for some shapes:
    - warm: 1.02–1.87×;
    - cold first build (characters the process has not seen yet): up to **2.30× on the host and 2.40× in the Linux container**.
  - The CHANGELOG says "up to about 2.3 times". The container figure is a hair above that (CJK 1000 × 8, ≈ 17 ms extra once per new character set).
  - The cost is charged and bounded (§7, §8). Accept it, or reword the CHANGELOG to "about 2.4×".
- **N2 (documentation nit).**
  - `docs/security/2026-10-11-m5-security-review.md` §2 row S3 still says a deny list's build costs "at most about 1.9 times measured".
  - That understates the cold-cache first build (2.3–2.4×), which the CHANGELOG states.
- **N3 (consistency, not security).** `POST /policies/validate` answers a refused policy with 400 and `{"detail"}` only: no `code`, unlike the engine routes' `bad_request`. The message is value-free and names the rule.

**Cross-lens note for the gates verifier's B1** (finalize dies on `PrepareFailure("policy_invalid")` for `@name` and inline policies):
- B1 is the F-18 seal working as designed: `dps_job.spec` holds only the sealed label, as the live counts above show.
- **X1:** the fix must make `_finalize` reopen the sealed body from `spec_enc` (as the run and discover workers already do). It must never write the policy body back into `dps_job.spec` in clear, and never relax `redact_spec`.
- `tests/api/test_inline_policy_sealed.py` must stay green with the fix.

## 12. Re-running

```bash
W=~/.cache/dps-build/reports/FINAL-security-r1; R=/Users/neelu/document_intelligence/data-privacy-services
$W/run_pytest_security.sh                                    # §1 run 1 (needs the stack's dps-db)
cd $W && source env.sh
for p in route_auth tenancy tenancy_extra tenancy_more sec4_sec5 redos redos_filter extra ssrf_extra; do
  $R/.venv/bin/python probe_$p.py; done                      # live probes (main stack, default ports)
env -i HOME=$HOME PATH=$PATH $R/.venv/bin/python probe_posture.py   # clean env; uses docker run --rm
cd $R && PYTHONPATH=. .venv/bin/python $W/trie_vs_round1_matrix.py && PYTHONPATH=. .venv/bin/python $W/cold_matches_build.py
docker compose exec -T app python -I - < $W/trie_vs_round1_matrix.py   # and cold_matches_build.py
```

**Probe rows.** The `results/*.json` files hold **133 passing rows and 2 failing rows**. Both failing rows were mistakes in my own probe, and each was re-probed correctly; neither is a product failure:
- `redos.json`: the last row expected 422 for the static filter on `/detect`. The documented code is 400 "invalid policy". `redos_filter.json` checks it correctly on five routes: 5/5.
- `tenancy.json`: `GET /policies/{name}/diff` was called without its required `from`/`to` query. `tenancy_extra.json` repeats it with the query: the foreign key gets 404, the same as a missing name.
