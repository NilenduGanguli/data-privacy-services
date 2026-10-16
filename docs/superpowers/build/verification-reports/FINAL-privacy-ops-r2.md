# FINAL — v1.0.0 candidate on `main` — PRIVACY-OPS verifier, round 2

**Verdict: PASS. No blocking finding.** Round 1's blocker B1 is fixed. With the provider faked at `LlmGateway._stellar` and the egress check set to strict, T3 labelling now reaches the provider:
- on **10 of 10** seeded `bank` tables (round 1: 5 of 10);
- on every L1 and L3 collision case (`PASSPORT`, `AADHAAR`, `PRIVATE`, `ACCOUNT`, a selected `Passport` or `Private`).

Every prompt sent was value-free. Two checks show it:
- a data-only check that applies neither of the fix's exemptions;
- a proof that every system message sent was the constant labelling prompt.

The other lens checks are also clean:
- container logs: 0 demo, source, canary and key-material hits;
- `dps_llm_raw_pii_sent_total`: 0;
- audit rows: value-free;
- gitleaks over the whole history: clean.

| | |
|---|---|
| Date | 2026-10-16, 07:00:16–07:11:40Z (12:30–12:42 IST) |
| Tree verified | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ **`d033dd3`**. This is round 1's `45be8ac` plus the fix commits `c7ad14f` and `d033dd3`. The probes imported `dps` 1.0.0 from this checkout (`.venv`, editable install). |
| Stack | main's `full` stack, started by gates-r2 at about 06:11Z and running images built from `c7ad14f`. `d033dd3` touches only `HANDOFF.md` and the `.pptx`. I did not restart or rebuild it. `/readyz` reported `ready: true` at the end. Other round-2 lens verifiers were using it at the same time: audit rows grew from 5,147 to 6,374 during my run. |
| Evidence | `~/.cache/dps-build/reports/FINAL-privacy-ops-r2/`. **Scripts:** `privacy_scan.py`, `probe_t3.py` (round 1's, plus stricter checks), `probe_t3_l2_all.py`, `probe_t3_collision.py`, `probe_egress_boundary.py`, `probe_locale_echo.py`, `locate_scan_field_hits.py`, `audit_structure.py`, `run_t3.sh`, `run_probe.sh`. **Outputs:** `privacy-scan-r2{a,b}.json`, `t3-summary.json`, `t3-l2-all.json`, `t3-collision.json`, `egress-boundary.json`, `audit-structure.json`, `scan-field-hits.json`, `locale-echo.json`, `gitleaks-*`, junit files and run logs. |
| Repo files modified | **None.** `git status --porcelain` and the `--ignored` set were the same at start and end. The only untracked path is `docs/screenshots/v1.0.0/`, which belongs to another verifier. pytest ran with `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`, and the `DPS_TEST_*` defaults of contract §12.2 were exported. |
| Values | Only counts, ids, column names, env-var names and file names are recorded. Prompts, canaries and key material stayed in memory. Scans of my own output files: 0 hits in all 29 files the final scan covered; 0 hits for the fixed parts of the probes' own canaries in every T3 run log. |
| Leftovers | No `dps_test_*` or `dps_fresh_*` database remains, and no probe schema remains in source-pg. Demo-site D4 during the L3 crawl: `non_get` 8 → 8 (+0). |

## 1. Log scan of every container (`privacy_scan.py`: r2a at 07:02Z, r2b at 07:10Z, after all probes)

**Scope:** all **15** containers of project `data-privacy-services`, including the exited one-shots. Each container's whole `docker logs` output was scanned, stdout and stderr: **104,324 lines** in r2b, 99,121 in r2a.

**Needles** (memory only):
- **Demo values:** the 14 forms of `demo/onboarding/data.json`.
- **Source values:** **182,193** identifying values from PG `bank.customers`, Oracle `CRM.CUSTOMERS` and Mongo `crm.profiles`, 20,000 rows each. They are matched as runs of up to 6 words.
- **Canaries:** 10, sent through the live app in each scan run.
- **Strong key material (49 needles):**
  - `DPS_JOB_KEK`, raw and as hex, b64 and b64url;
  - `DPS_BOOTSTRAP_API_KEY`, `DPS_TOKENIZER_TOKEN`, `DPS_PG_WORKER_PASSWORD`, `ORACLE_PASSWORD`, `MOCK_TOKENIZER_KEY`;
  - the app's mounted secret files;
  - the 3 demo API keys;
  - every DEK and seed in `var/demo/*.json`;
  - **the DEK and the synthesis seed the live app returned to the canary calls**.
  - Each needle was also searched URL-encoded and JSON-escaped.
- PEM private-key markers.

Self-test: one planted line per needle class was found, 4 of 4.

| Class | Hits in logs (r2a / r2b) |
|---|---|
| Strong key material (incl. the live DEK and seed) | **0 / 0** |
| PEM `BEGIN … PRIVATE KEY` | **0 / 0** |
| Canaries | **0 / 0** |
| Demo values (incl. the name tokens) | **0 / 0** |
| Seeded source values | **0 / 0** |
| "Weak" secrets (dev login literals) | 54 / 54. These are identifiers, not leaks (checked as booleans below). |

The 54 weak hits break down as follows:
- `POSTGRES_PASSWORD` == `postgres`: source-pg 19, dps-db 15.
- `DPS_PG_MIGRATION_PASSWORD` == its own login: dps-db 4.
- The secret file `src/bank-pg-ro/username` == `dps_reader`, which is a source-pg role name: source-pg 12.
- `MINIO_ROOT_PASSWORD` == `minioadmin`: 4, all from MinIO's own startup line.

**Canary traffic through the live app** (tenant `retail-banking-apac`, demo keys):

| Call | Status | Result |
|---|---|---|
| `obfuscate` | 200 | `key_material` returned, 0 canaries echoed |
| `obfuscate` with that key passed back | 200 | — |
| `synthesize` | 200 | seed returned |
| `detect` | 200 | — |
| Bad preset (a canary name) | 400 | echoes 0 |
| Canary-made API key | 401 | not echoed |

The scan's `obfuscate_422` call (`options.locale` = a canary e-mail) answers 200 with 2 canaries in the body. `probe_locale_echo.py` locates them: they are the made-up `ref_code` and the `notes` text, which pass through in `$.result`. No detector recognises them on this NER-less stack. All 5 PII fields (name, e-mail, mobile, PAN, account) were transformed, and the locale is **not** echoed. Plain `obfuscate` and the `gdpr-pseudonymise` preset behave the same. Round 1 had the same outcome.

**At rest** (all 26 `dps.*` tables, 21,736 rows in r2b):
- 0 strong key material, 0 canaries, 0 demo values. The live DEK and seed are stored nowhere, so **D2 holds**.
- KCV hits: 10, in `dps_job` and `dps_job_group`. These are key check values (migration 046) and are there by design.
- `postgres` appears as the engine name.
- 11 source hits in `dps_scan_field.fill_synthesize/fill_obfuscate` (N3).

## 2. LLM exposure: `dps_llm_raw_pii_sent_total` per tenant

- **Live stack:** `dps_llm_raw_pii_sent_total` is **0.0** on `app`, `worker` (9400) and `browser-worker` (9401), in both scans.
  - No `dps_llm_*_by_tenant` series exists. The stack runs the `stub` provider, and T3 never calls a stub.
  - No audit row of any tenant carries `llm_*` counts. The 5 tenants are `default`, `retail-banking-apac`, `_system`, `fsr1-a` and `fsr1-b`. So every tenant is at 0 calls, 0 raw and 0 blocked (N1).
- **In process, provider faked at `_stellar`, strict:** the per-tenant counters move as specified.

| Probe | Tenant | `calls_by_tenant{label}` | `raw_pii_sent_by_tenant` | `exposure_blocked_by_tenant` |
|---|---|---|---|---|
| L1 HTTP (static keys) | `fpo-tenant-a` | +3 | 0 | 0 |
| L1 HTTP | `fpo-tenant-b` | +1 | 0 | 0 |
| L2a ledger (200 real seeded customers + 5 ambiguous columns) | `t1` | +1 | 0 | 0 |
| L2b seeded `bank.customers` (round 1: **blocked**) | `t1` | **+1** | 0 | **0** |
| L3 crawl of the demo site | `fpo-l3` | +8 | 0 | 0 |

Process counters in the same runs: `dps_llm_raw_pii_sent_total` +0; `dps_llm_calls_total{stellar,label,ok}` +4 (L1), +1 (L2a), +1 (L2b), +8 (L3); `…blocked` +0 everywhere.

The in-process audit events (MemoryAuditSink) of the 4 L1 calls carry `llm_calls 1, llm_raw_values 0, llm_blocked 0` and 0 needle hits.

## 3. Audit rows are value-free (`dps.dps_audit_log`, all partitions)

- **6,374 rows** (r2b), scanned with every needle class of §1: **0 hits**. This includes the canaries, the live DEK and seed, the demo values and the 182,193 source values.
- Structure (`audit_structure.py` over 6,368 rows):
  - every `counts` value is an `int`, except `verification_passed`, which is a `bool`;
  - `findings_by_type` and `methods_used` are objects with **0 non-numeric nested values**;
  - the `field.*` connection-change keys are ints.
- `route`: 42 distinct. 38 are HTTP templates (`/api/v1/artefacts/{artefact_id}`, …). The other 4 are internal action names, each a literal in `dps/` source (checked as booleans).
- `policy`:
  - `null` 3,316;
  - `name` or `name@vN` 2,465;
  - `<base>+inline:<12-hex>` 218;
  - `-` 367;
  - one composed preset form, 2 rows, whose parts are `dps/` literals.
  - A caller-supplied preset is never recorded verbatim.
- `key_id`: key names, not key material (0 strong-key hits).

## 4. gitleaks over the whole history

- Image `zricethezav/gitleaks@sha256:c00b6bd0…` (v8.30.1, the image `autopush.sh` uses). Command: `git /repo --log-opts=--all --redact`, with the repo mounted read-only.
- **With `.gitleaksignore`: "no leaks found", exit 0.**
  - **457 commits scanned**: 36 refs, 465 non-merge commits. Round 1 scanned 455; the difference is `c7ad14f` and `d033dd3`.
  - The 8 commits that change only binary files are the same 8 as in round 1 (screenshot PNGs, `gitleaks-binary-only-commits.txt`). They have no text patch (N5).
- **Without the ignore file** (`.git` mounted alone): **14 findings** (generic-api-key 9, curl-auth-header 3, jwt 2). All are redacted test fixtures and doc examples.
  - All 14 fingerprints are in `.gitleaksignore`, which has 14 entries.
  - **0 new and 0 stale**. The two fix commits added no finding.

## 5. T3 labelling reaches a provider faked at `_stellar` under strict egress, with value-free prompts

**How the probes work.** `LlmGateway._stellar` is patched on the class, so the product's own gateways run the production `complete()` with the verifier. That verifier is the only egress point and blocks before dispatch (checked in `gateway.py`). The seam asserts `llm_egress_check == "strict"` on every prompt.

**New in round 2: two independent checks on every prompt sent.**
- **(a) `system_identity`**
  - Every prompt has the roles `system/user`, with exactly one system message.
  - All system messages are byte-equal to `llm_label.system_prompt()`: 1 distinct sha256, `f744996ecf40`.
  - So the fix's system-message exemption covered no call data.
- **(b) `strict_data_hits`**
  - Every needle of 3 or more characters is looked for in the non-system messages, with **neither exemption** applied: as a whole word, or (5+ characters, 6+ for digits) as a substring.
  - Each hit is classified as a field name, a DPS metadata token, a value shape (the value itself is only `x/X/9` and punctuation) or **other** (a raw value).
- The product's own `residual_values(messages, protected=<every input value>)` was also run.

| Layer / probe | Provider calls | Blocked | `strict_data_hits` other | Verifier residual (all inputs protected) | Outcome |
|---|---|---|---|---|---|
| **L1** HTTP in process, auth on. 41 needles: demo values + canaries. Routes: `/obfuscate` ×2 (two tenants), `/detect`, NDJSON stream of 3 | 4 | 0 | **0** (0 hits) | 0 | `cust_ref_cd` CUSTOMER_ID, `rm_code` EMPLOYEE_ID and `relationship_nm` PERSON_NAME labelled (score 0.85) and transformed; the 0.4 verdict was dropped; one call per stream |
| **L2a** `discover_job`: 200 real seeded customers + 5 ambiguous columns (1,921 needles) | 1 | 0 | **0** (0 hits) | 0 | 3 columns labelled. The one snapshot "hit" is the sensitivity label `MEDIUM` at `$.columns[n].sensitivity`, the probe's own made-up risk band |
| **L2b** `discover_job` on the seeded **`bank.customers`** (500 sampled; needles = all 20,000 rows, 168,491 values) | **1** | **0** | **0**. 1 hit, classified as a value shape: `risk_score` holds 999 = shape `999` | 0 | Round 1: blocked. 8 leftover columns sent |
| **L2, all 10 seeded `bank` tables** (500 sampled each; needles = every row: 500 to 200,000 rows, up to 629,350 values per table) | **10/10** | **0** | **0** on every table | 0 on every check | Round 1: 5/10 blocked. `t3-l2-all.json` |
| **L1 / L3 collisions** (`probe_t3_collision.py`, 17 cases): `PASSPORT`, `AADHAAR`, `PRIVATE`, `ACCOUNT`; L3 selected `Passport` / `Private` / placeholder / `X1` | 17 / 17 | 0 | — | `llm_raw_values` 0 | `ref_cd` and `member_ref` labelled CUSTOMER_ID in every case. Round 1: the collision cases were blocked |
| **L3** in-process crawl of the demo site (`/apply`, 21 pages; 32 page-protected strings + demo values) | 8 | 0 | **0** (0 hits) | 0 | `residence` LOCATION and `income` AMOUNT labelled; D4 `non_get` +0 |

**Hits that the independent system-plus-data check (round 1's `value_hits`) finds and the data-only check does not.** These are 1 each in `customers.id_type`, `customers.segment`, `contacts.channel`, `kyc_documents.doc_type` and `loans.product`. Each is a category code that sits inside a taxonomy type name of the constant prompt (counted only: for `id_type` and `segment`, 1 of 4 distinct values each, both inside a type name). This is exactly the collision the fix removes, and the prompt is data-independent.

**Method change.** Round 1's L2 assertion allowed a hit only as a field name or as a whole word of the system text. It does not fit the fixed design: `PRIVATE` sits inside `PRIVATE_KEY`, and `999` is a shape. It failed once (run 1: `t3-run1.log`, `t3-summary-run1.json`; the probe results were identical). I replaced it with (a) + (b) above, which are stricter for call data. The rerun passed: **3 passed**.

**The exemption boundaries hold** (`probe_egress_boundary.py`, made-up values):
- **Scanned in full, residual ≥ 1:** a system message equal to a fixed prompt plus one space; a fixed prompt with call data appended; ad-hoc system text.
- **Exempt, residual 0:** the exact labelling, rewrite and spec prompts.
- `PASSPORT` in the user message still counts.
- `162` and a 7-digit value count; `999` and `9999999999` do not (value shapes).
- An e-mail counts through the pattern scan.
- A rate of `0.16` leaves no 3-digit word.
- **End to end through `complete()` under strict:**
  - the exact prompt is sent;
  - the prompt with one extra space and the `PASSPORT` user message are **blocked, nothing sent**;
  - an all-nines value is sent.

## 6. Non-blocking notes

- **N1 (round 1, still open).** The live stack never exercises the per-tenant LLM counters: it runs the stub provider, rewrite is off, and T3 never calls a stub. "0 per tenant" live therefore means that no series exists. The counters are proven in process (§2). Only a live run with the Stellar mock would prove them at stack level.
- **N2 (round 1, still open).** Dev literals equal login or engine names (`postgres`, the migration login, `dps_reader`, `minioadmin`). MinIO logs its default credentials at startup. These are dev-only, but a log-scan gate has to treat them as identifiers.
- **N3 (round 1; now 11 cells in 3 scans).** In 11 cells, the persona name or city the scanner filled is the same string as a seeded customer's name or city.
  - The cells are in `fill_synthesize` and `fill_obfuscate`, with `persona_attr` name or city. One scan is round 1's `s_01M51G97…`; the other two are from gates-r2 (`s_01M51QPW…` default, `s_01M51QTF…` retail-banking-apac S9).
  - Both sides come from DPS's own `SurrogateEngine` pools: `tools/seed_model.py::seed_customers` builds the seeded names with it.
  - The scanner crawls the demo site and never reads the source, so these values are not derived from source data.
  - With real customer data, common names will coincide in the same way. If persona-vs-source uniqueness is ever wanted, it needs a deny-list.
- **N4 (new; design review of the fix).** A protected value written only in `x`, `X`, `9` and punctuation is no longer looked for (`egress._SHAPE`).
  - Such a value is identical to its own shape, which T3 sends by contract. Sending it therefore discloses nothing beyond the permitted shape.
  - The class is narrow: sentinels such as `999` and `9999999999`, and masks such as `XXXX`.
  - One instance in the seeded data: `customers.risk_score` 999.
  - This is not a weakening in substance. The contract amendment and the egress docstring record it.
- **N5 (round 1, still open).** gitleaks reads text patches only. The 8 binary-only commits (screenshots) are not secret-scanned; images belong to the OCR and capture lens.
- **N6 (new, outside this lens).** `options.locale` set to an arbitrary string (an e-mail) is accepted with 200. It is not echoed, so there is no privacy impact; the input-validation question belongs to the L1 lens.
