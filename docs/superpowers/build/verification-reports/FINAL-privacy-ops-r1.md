# FINAL — v1.0.0 candidate on `main` — PRIVACY-OPS verifier, round 1

**Verdict: FAIL. One blocking defect (B1).** Everything else in this lens is clean:
- the container logs;
- the per-tenant LLM exposure counters;
- the audit rows;
- gitleaks over the whole history.

**B1.** T3 labelling does not reach the provider under `DPS_LLM_EGRESS_CHECK=strict` when a protected original coincides with a word of T3's fixed system instructions. In L2 the verifier also counts a short number that matches DPS's own metadata. This happens on half of the seeded demo `bank` tables, and also in L1 and L3.
- The system blocks the call. It fails closed: no value is sent and none leaks.
- But T3 does not work on realistic data. That is the property the `fllm` fix set out to restore.

| | |
|---|---|
| Date | 2026-10-16, 04:26–04:50Z (09:56–10:20 IST) |
| Tree verified | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ **`45be8ac`** (`chore(release): v1.0.0`). The probes imported `dps` 1.0.0 from this checkout (`.venv`, editable). |
| Stack | main's `full` stack, already running (rebuilt by the release step, left by the gates verifier). I did not restart or rebuild it. `/readyz` was ready at the end. Six other lens verifiers were using it at the same time (console, deploy-demo, l1-docs, l3-scanner, security, synthetic). |
| Probes / evidence | `~/.cache/dps-build/reports/FINAL-privacy-ops-r1/`. Scripts: `privacy_scan.py`, `probe_t3.py`, `probe_t3_diag.py`, `probe_t3_collision.py`, `probe_t3_l2_all.py`, `run_t3.sh`. Results: `privacy-scan-r{1,2,3}.json`, `t3-summary.json`, `t3-diag.json`, `t3-collision.json`, `t3-l2-all.json`, `gitleaks-all*.json`, plus the run logs. |
| Repo files modified | **None.** `git status` was empty at the start. At the end it shows only `?? docs/screenshots/v1.0.0/` (console screenshots, last written 10:10 IST by another verifier) and an ignored `dps/evaluation/__pycache__/` (10:03:55 IST; nothing I ran imports `dps.evaluation`). Neither is mine. Every pytest run used `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`. Two one-line `import dps…` checks ran without it; they could at most refresh git-ignored `__pycache__` files. |
| Values | Only counts, ids, column, env-var and file names are recorded. Prompts, canaries and key material stayed in memory. A scan of my own 28 output files finds 0 demo, source, canary or key hits. |
| Leftovers | My test databases and source schemas were dropped by the fixtures. The one `dps_test_91760` left at the end belongs to another verifier's running pytest. Demo-site D4 counters: `non_get` 16 → 16. |

## 1. Log scan of every container (`privacy_scan.py`, final run r3 at 04:49Z; r1 and r2 agree)

**Scope:**
- all 15 containers of project `data-privacy-services`, including the exited one-shots;
- each one's whole `docker logs` output, stdout and stderr, from the stack start at 02:48:50Z;
- **55,169 lines** in total.

**Needles** (memory only):
- **Demo values:** the 14 forms of `demo/onboarding/data.json`, plus the digit forms of values with 6 or more digits.
- **Source values:** **182,193** identifying values from PG `bank.customers`, Oracle `CRM.CUSTOMERS` and Mongo `crm.profiles`, 20,000 rows each.
  - Every column was used, keeping values that appear at most 3 times and have at least 8 characters (at least 9 digits for a number).
  - Dates were left out, because they collide with log timestamps.
  - These are matched as runs of up to 6 adjacent words.
- **Canaries:** 10 fresh canaries sent through the live app in the same run (§1.1).
- **Strong key material (49 needles):**
  - every secret-named env value of every container (`DPS_JOB_KEK` also as hex, base64 and base64url; `DPS_BOOTSTRAP_API_KEY`, `DPS_TOKENIZER_TOKEN`, `DPS_PG_WORKER_PASSWORD`, `ORACLE_PASSWORD`, `MOCK_TOKENIZER_KEY`, …);
  - the app's mounted secret files;
  - the 3 demo API keys;
  - every DEK and synthesis seed in `var/demo/*.json`;
  - **the DEK and the synthesis seed that the live app returned to my canary calls**.
  - Each needle was also searched URL-encoded, JSON-escaped and in base64url form.
- PEM private-key markers.

A self-test line holding one needle of each class was found 4/4.

| Class | Hits in logs |
|---|---|
| Strong key material (incl. the live DEK and seed) | **0** |
| PEM `BEGIN … PRIVATE KEY` | **0** |
| Canaries (names, e-mail, mobile, PAN, account, ref) | **0** |
| Demo values | **0** |
| Seeded source values | **0** |
| "Weak" secrets (local login literals) | 32. These are identifiers, not leaks (below). |

The 32 weak hits are dev literals that equal a login or engine name. I checked them as booleans only:
- `POSTGRES_PASSWORD` == `postgres` (source-pg 12, dps-db 10);
- `DPS_PG_MIGRATION_PASSWORD` == its login (dps-db 2);
- a `secret_ref` `username` file, `dps_reader` (source-pg 6);
- `MINIO_ROOT_PASSWORD` == `minioadmin`: MinIO's own startup line, 2 hits (N2).

Error-level lines are not in this lens. The gates report covers them.

### 1.1 Canary traffic through the live app (tenant `retail-banking-apac`, demo keys)

Each request below was answered as shown, and none echoed or logged a value:
- `obfuscate` returned 200, with `key_material` returned to the caller.
- `obfuscate` again, with that key passed back as `options.consistency.key_material`, returned 200.
- `synthesize` returned 200, with a `synthesis_seed` returned.
- `detect` on free text holding the canary name and e-mail returned 200.
- A bad policy reference (a canary name as the `preset`) returned 400; the body echoed 0 canaries.
- A canary made into an API key returned 401, and the response did not echo it.

After these calls:
- logs: 0 canary, 0 DEK and 0 seed hits;
- audit rows: 0;
- all 26 `dps.*` tables: 0 for the DEK and seed. **D2 holds: DPS stores no key it returns.**

An early attempt (r1) put the name into `notes` too. The engine answered 422 `leakage` ("output failed the leakage sweep; nothing was returned"): fail closed on this NER-less stack (N4).

### 1.2 Tables at rest (all 26 `dps.*` tables, 15,125 rows)

- 0 strong key material, 0 canaries, 0 demo values.
- KCVs appear 4 times, in `dps_job` and `dps_job_group`. That is by design (migration 046; a key check value is not key material).
- Weak `postgres` hits are the engine name in connection, job and evidence rows.
- **2 source hits in `dps_scan_field.fill_synthesize`**, located by JSON path:
  - another verifier's scan `s_01M51G97KKWCDJGH4066MZY7C6`, field `fullName` (PERSON_NAME), pages 1 and 9;
  - the *synthetic* persona name the scanner filled equals one of the 20,000 seeded synthetic customer names, because both come from the same name pools;
  - the value is not derived from the source (N3).

## 2. LLM exposure: `dps_llm_raw_pii_sent_total` per tenant

- **Live stack.** `dps_llm_raw_pii_sent_total` is **0.0** on `app`, `worker` (ops 9400) and `browser-worker` (ops 9401), at the start and in every scan.
  - No `dps_llm_*_by_tenant` series exists at all. The stack runs the `stub` provider, and T3 never calls a stub. `DPS_LLM_TEXT_REWRITE=false`. So no LLM call happened, and the per-tenant raw count is 0 for every tenant (N1).
  - Audit rows (4,279 at r3; tenants `default`, `retail-banking-apac`, `_system`, `fsr1-a`, `fsr1-b`): no row carries `llm_*` counts, so every tenant is at 0 calls, 0 raw, 0 blocked.
- **In process, provider faked at `_stellar`, strict:** the per-tenant counters move as specified.

| Probe | Tenant | `calls_by_tenant{label}` | `raw_pii_sent_by_tenant` | `exposure_blocked_by_tenant` |
|---|---|---|---|---|
| L1 via HTTP (static keys, 2 tenants) | `fpo-tenant-a` | +3 | 0 | 0 |
| L1 via HTTP | `fpo-tenant-b` | +1 | 0 | 0 |
| L2 `discover_job` under `Consumer._guard` | `t1` | +1 (a), +0 (b, blocked) | 0 | +1 (b: **B1**) |
| L3 crawl under `Consumer._guard` | `fpo-l3` | +8 | 0 | 0 |

Process counters in the same runs: `dps_llm_raw_pii_sent_total` +0; `dps_llm_calls_total{stellar,label,ok}` +4 (L1), +1 (L2a), +8 (L3); `…blocked` +1 (L2b only).

## 3. Audit rows value-free (`dps.dps_audit_log`, all partitions)

- **4,279 rows** (r3), scanned with every needle class of §1: **0 hits**, including the canaries and the live DEK and seed sent minutes earlier.
- Structure:
  - `route` is a template (`/api/v1/artefacts/{artefact_id}`, …; 42 distinct);
  - `policy` is a name, `name@vN` or `<base>+inline:<12-hex hash>` (22 distinct; a bad caller-supplied preset is not recorded verbatim);
  - every `counts` value is a number, except `verification_passed` (a boolean) and `findings_by_type` / `methods_used` (objects whose values are all numbers);
  - the connection-change keys `field.*` are numbers.
- The in-process L1 audit events (MemoryAuditSink) carry `llm_calls 1, llm_raw_values 0, llm_blocked 0`, and 0 needle hits.

## 4. gitleaks over the whole history

- Image `zricethezav/gitleaks@sha256:c00b6bd0…` (v8.30.1, the image `autopush.sh` uses); `git /repo --log-opts=--all --redact`, repo mounted read-only.
- **With `.gitleaksignore`: "no leaks found", exit 0.** It scanned 455 commits:
  - all 36 refs, 463 non-merge commits;
  - the 8 commits that change only binary files (console screenshot PNGs, listed in `gitleaks-binary-only-commits.txt`) have no text patch (N5).
- **Without the ignore file** (`.git` only, mounted): exactly **14 findings** (generic-api-key 9, curl-auth-header 3, jwt 2).
  - All 14 fingerprints are in `.gitleaksignore`, and all 14 entries are used: **0 new and 0 stale**.
  - The reviewed findings are test fixtures and doc examples.

## 5. T3 labelling reaches a provider faked at `_stellar` under strict egress, with value-free prompts

**How the probes work:**
- `LlmGateway._stellar` is patched on the class, so the product's own gateways run the production `complete()` with the egress verifier.
- The seam asserts `llm_egress_check == "strict"` on every prompt it receives.
- Two value-free checks run on every prompt:
  - my own normalised needle check against every input value;
  - the product's `residual_values(messages, protected=<every input value>)`.

| Layer / probe | Provider calls | Blocked | Value hits in prompts | Verifier residual (all inputs protected) | Outcome |
|---|---|---|---|---|---|
| **L1** HTTP in-process, auth on. Records: the demo values plus ambiguous canaries. Routes: `/obfuscate` ×2 (two tenants), `/detect`, NDJSON `/detect` stream of 3 | 4 | 0 | **0** / 41 needles | 0 | `cust_ref_cd`, `rm_code`, `relationship_nm` labelled (score 0.85, `llm_label`) and transformed; the 0.4 verdict was dropped; one call per stream |
| **L2a** `discover_job`: 200 **real seeded customers** plus 5 ambiguous columns | 1 | 0 | **0** / 1,921 | 0 | `cust_ref_cd`, `rm_code` and `beneficiary_nm` labelled; `progress.metrics.llm` {1, 0, 0}; the stored snapshot is value-free (its one "hit" is the sensitivity label `MEDIUM`, located by JSON path) |
| **L2b** `discover_job` on the seeded **`bank.customers`** (500 rows) | **0** | **1** | — | — | **B1**: 8 leftover columns, never sent |
| **L2, all 10 seeded `bank` tables** (500 rows each) | 5 tables | **5 tables** | 0 | 0 | **B1** |
| **L3** in-process crawl of the demo site (`/apply`, 21 pages) | 8 | 0 | **0** / 32 page-protected strings + demo values | 0 | `residence` → LOCATION and `income` → AMOUNT labelled; D4 `non_get` 16 → 16 (+0), GET +175 |

## 6. B1 (blocking): the strict egress verifier stops T3 labelling on realistic data

**Symptom.**
- **L2 discovery.** On main's seeded demo `bank` schema (`probe_t3_l2_all.py`), the T3 call of **5 of 10 tables** is stopped: `contacts`, `customers`, `kyc_documents`, `loans`, `transactions`.
  - The log shows `llm_egress_blocked purpose=label` and `llm.label fallback=egress_blocked`.
  - The job's `progress.metrics.llm` is `{calls 0, blocked 1}`.
  - None of those tables' leftover columns is labelled. They include `kyc_documents.doc_ref` (a document reference), `transactions.description` and `kyc_documents.notes`.
- **L1** (`probe_t3_collision.py`):
  - A record such as `{"kyc": {"doc_kind": "PASSPORT", "ref_cd": "AB-1234-77"}}` gets no T3 call (`llm_blocked 1`, 0 provider calls).
  - The same happens with `AADHAAR`, `PRIVATE` or `ACCOUNT`.
  - The control (`"X1"`) is sent, and `ref_cd` is labelled CUSTOMER_ID.
- **L3:** a page whose select **shows** `Passport` (or `Private`) as the chosen option gets no T3 call. Unselected options and the placeholder do not block.

**Root cause** (`dps/llm/egress.py::residual_values`, unchanged since `0fd76d0`). Two collisions, measured per stopped call in `t3-l2-all.json` / `t3-diag.json`.

1. **Protected originals are looked for in every message, the fixed system message included** (`0fd76d0` moved only the *pattern* scan to call data).
   - The labelling system prompt lists the taxonomy type names.
   - A protected value of 4 or more characters counts as a substring of it; a 3-character one counts as a whole word.
   - The protected values are every sampled or shown value of the leftover fields. So common categorical codes count as "raw values found":
     - ID-type and document-type codes (`customers.id_type`: 4 type names; `kyc_documents.doc_type`: 2);
     - contact and transaction channels;
     - segments and products (e.g. a value inside `PRIVATE_KEY` or `ACCOUNT_NUMBER`).
   - This alone stops all 5 tables.
2. **L2 only: short protected numbers equal DPS's own metadata tokens in the call data.**
   - `bank.customers.risk_score` has 3-digit values. They match as whole words:
     - the shape `999`, which `value_shape` gives every 3-digit number;
     - the fraction digits of `hints.null_rate` / `hints.distinct_ratio` (`0.162` → word `162`).
   - `customers`: 8 residual values in all, **3 of them in call data**. So exempting the system message alone does not unblock `customers`.

**Status.** This is the "Known residual, not changed here (owner of `fllm`)" of `docs/superpowers/build/M5-DEVIATIONS.md` (Task 8B, S3b: 5 of 11 lane tables). It was never fixed, and it is not in CHANGELOG's known limitations or in HANDOFF.

**Impact:**
- Under `strict`, which is required in staging/prod, T3 is opt-in but silently unavailable for any table, record or page that holds such a code. KYC data (ID-type and document-type columns) is the typical case.
- The T3 tests miss it because their fixtures avoid such values.
- No privacy property is weakened. Every stop is counted (`dps_llm_exposure_blocked_by_tenant_total`, job and scan `llm.blocked`).

**Fix (suggested; tests first):**
1. **Protected originals: scan the call data only.** In `residual_values`, look for protected originals in the non-system messages, as the pattern scan already does. Keep the exemption safe: exempt a system message only when it is one of the registered constant prompts (`llm_label.system_prompt()`, genspec, rewrite; tests already pin them). Any other system text keeps the full scan.
2. **L2 numeric collisions.** A protected value that is itself a value shape (only `9`/`x`/`X` and punctuation), or a short digit-only value, should not count as a match inside DPS-generated `shapes` and `hints` tokens. Alternatively, render the rates so that they leave no bare digit words. This decision belongs to the fix round. The existing tests ("a prompt with a real value is never sent") must stay green.
3. **Red-first tests:**
   - `discover_job` with T3 under strict on each seeded `bank` table reaches the provider (10/10);
   - an L1 record holding `PASSPORT` or `ACCOUNT` gets its leftover labelled;
   - an L3 page with a selected `Passport` option gets its leftover labelled.
   - Then re-run `probe_t3_l2_all.py` and `probe_t3_collision.py` (both run against any checkout via `run_t3.sh`-style invocation).
4. If the owner accepts the limitation for v1.0.0 instead, record it in CHANGELOG "Known limitations" and in HANDOFF, together with the 5/10 figure.

## 7. Non-blocking notes

- **N1** The live stack never exercises the per-tenant LLM counters. It runs the stub provider, rewrite is off, and T3 never calls a stub. "0 per tenant" there means no series at all; the counters are proven in process (§2). A live check with the Stellar mock would be the only stack-level proof.
- **N2** Local dev literals equal login or engine names (`postgres`, `dps_owner`, `minioadmin`). MinIO logs its default credentials at startup. These are dev-only, but a log-scan gate has to treat them as identifiers.
- **N3** The scanner's synthetic persona name equalled a seeded synthetic customer name twice (shared name pools). It is not derived from the source and is not a D-A table. If persona uniqueness against source data is ever wanted, it needs a deny-list.
- **N4** On this NER-less stack, an L1 record whose `notes` repeats its `full_name` fails closed with 422 `leakage`. This is correct behaviour and echoes nothing; it is a usability point for the L1 lens.
- **N5** gitleaks reads text patches only, so binary-only commits (8, screenshots) are not secret-scanned. Images belong to the OCR and capture lens.
