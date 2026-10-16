# S1-M3: post-merge verification of main after the M3 merge (lens "privacy-console", round 1)

**Verdict: FAIL.** There are 2 blocking findings. Both are on the Evidence page of a **MongoDB** run, and both
have the same root cause: a nested document leaf is keyed by its top-level field name. B1 is in the console and
B2 is in the backend pack builder. Both fixes are small.

Everything else in this lens is green:

- **Logs:** 0 demo values and 0 key material in every one of the 13 container logs, including the 22.6 MB mongod log.
- **Driver loggers at DEBUG:** they leak nothing. A positive control shows the scan would see a leak.
- **LLM exposure:** raw PII sent to the LLM is 0 for every tenant. A control shows the per-tenant counters work.
- **Console walk:** 0 console errors, except the browser's own line for deliberate 422 refusals (N1). No
  horizontal scroll at 390 px.
- **Key material:** shown once, and never in storage, the URL, the DOM after "stored", the logs or the metadata rows.

| | |
|---|---|
| Date | 2026-10-14, 17:43–18:16 IST |
| Tree verified | `main` @ `144cc65`, clean at start and end. M3 merge `b878c50` plus integration fixes and docs. |
| Stack | Compose project `data-privacy-services` with default ports, left running by the gates verifier. Not restarted, rebuilt or brought down. `tokenizer-mock` is on 8430. Other lens verifiers were active at the same time (tenant `s1m3sec-a`, keys `s1m3sec-r344ab9-*`). |
| Browser | Private Playwright `playwright==1.55.0`, Chromium 1187. venv: `~/.cache/dps-build/pw-s1m3-privacy-console` (also holds httpx, asyncpg, pymongo, oracledb, pyahocorasick, pypdf, pillow). Never the shared Playwright MCP browser. |
| Probes, logs, shots | `~/.cache/dps-build/reports/S1-M3-privacy-console-r1/` (scripts at the top level, JSON in `logs/`, 173 screenshots in `shots/`) |
| Repository files modified | **none** (the console browser tests that write tracked shots were not run) |

## 1. Log scans: every main container, demo values and key material (counts only)

**Value corpus.** The corpus is built and held in memory only. It has 1,520,692 distinct values of 8 or more
characters, taken from every column with at least 200 distinct values:

| Source | Columns | Values |
|---|---|---|
| PostgreSQL `bank.*` | 45 | 996,588 |
| Oracle `CRM.*` and `LOANS.*` | 25 | 352,695 |
| MongoDB `crm.profiles` (every leaf path) | 10 | 171,409 |

Dates and timestamps are excluded. Each value is also searched in its JSON-escaped spelling. Matching uses
Aho-Corasick on whole tokens: a value inside a hash, a timestamp or a longer word does not count.

**Key-material needles** (36 in the scan made after the walk):

- 10 captured during the walk: both wizard job keys (`dek` and `wrapped_dek`), the API-built group key, the
  New-job-group dialog's `.dpskey` and `.dpsseed`, and the raw API key created on Admin.
- The secret-named environment values of every container.
- Every file under the app's `DPS_SECRET_DIR`, including each JSON leaf.
- The bootstrap-key literal and the KEK literal, both raw and decoded.
- Key-shaped regexes for `dps_` API keys and for 256-bit base64, base64url and hex strings.

**Targets:**

- `docker logs` (stdout and stderr) of all 13 containers of the project, running and exited.
- Every `dps.*` metadata table.
- The bodies of `/metrics` and `/readyz` on the app and on the worker.

There were three scans. The baseline ran at 17:58, before the walk. The second ran after the walk, in the walk's
own process with its key material. The final one ran at 18:14, after every probe. All three give the same result:

| Container (final scan) | Log bytes | Value hits | Secret-needle hits | Key shapes |
|---|---|---|---|---|
| app | 460,021 | **0** | 0 | 0 |
| worker | 89,529 | **0** | 0 | 0 |
| mongo | 22,582,315 | **0** | 0 | 0 |
| oracle | 66,606 | **0** | 0 | 0 |
| dps-db | 77,467 | **0** | 23 (third-party lines, see below) | 0 |
| source-pg | 22,878 | **0** | 39 (third-party lines, see below) | 0 |
| minio | 1,158 | **0** | 4 (MinIO's own warning) | 0 |
| tokenizer-mock, azure-ocr-mock, migrate, minio-init, seed-sources, demo-secrets | small | **0** | 0 | 0 |

**Where the secret-needle hits come from.** I checked the context of every hit with the needle masked
(`secret_ctx.py`). Every hit is in a third-party server's own line:

- dps-db and source-pg: `password authentication failed for user "<name>"`. The demo logins have username ==
  password, so the username matches the password needle.
- dps-db and source-pg: the word `postgres`, which is also the local superuser password, inside paths such as
  `/var/lib/postgresql/data` and in "owned by user" lines.
- minio: MinIO's startup warning about default credentials (the local `minioadmin` default).

There are 0 hits of any needle in the app or worker logs. There are 0 hits of the walk's 10 key-material needles
anywhere: no log and no metadata row.

**Metadata rows.**

- **0 values and 0 secret needles** in `dps_audit_log` (1,067 rows), `dps_evidence` (118), `dps_job` (234),
  `dps_job_group`, `dps_snapshot`, `dps_artefact`, `dps_connection`, `dps_api_key` and the policy, report,
  tenant and worker tables.
- The only "secret" hits are the word `postgres` as an engine name.
- The only hex-256 shapes are the sha256 config, pack and key hashes, as expected.
- `dps_job_partition` matches 6 values. These are the encoded `last_key` resume marks, which contract §4.1 stores
  there by design. Reported for information.

**`/metrics` and `/readyz`, app and worker.**

- 0 values.
- The only secret matches are a 6-character demo password that appears only inside longer `dps_…` metric names
  (0 whole-token matches).
- The 4 `dps_` "key shapes" are all lower-case metric names (`metrics_scan.py`).
- Metric label names are value-free: `entity_type`, `detector`, `method`, `mode`, `op`, `outcome`, `route` and so on.

**Positive controls.** Corpus values from different engines, planted into a copy of the app log, were found 2 of
2. Secret needles planted the same way were found 2 of 2.

## 2. Driver loggers at DEBUG (pymongo, oracledb)

`driver_debug_probe.py` was run with `docker exec -i` inside the **app** container and again inside the
**worker** container. It runs as its own process, so the running services are not touched. The setup:

- `dps.observability.configure_logging("DEBUG")`, exactly what `DPS_LOG_LEVEL=DEBUG` does.
- A root handler at DEBUG, so every stdlib record is captured, along with all of stdout and stderr.
- The `oracledb` logger forced to DEBUG as well.

The workload:

- **MongoDB through DPS:** `MongoConnector` describe, count, sample and `read_partition` of `crm.profiles`. The
  DPS `table` writer into a scratch database, made to hit E11000 on a real `customerId`.
- **MongoDB through raw pymongo:** inserts, finds, and a raw E11000.
- **Oracle:** `OracleConnector` describe, count, sample and `read_partition` of `CRM.CUSTOMERS`. Raw oracledb
  inserts of real values into a scratch table, a select, and an ORA-00001 on a real value.

| | App | Worker |
|---|---|---|
| Demo values held (memory) | 1,905 | 1,900 |
| **Values in the captured output** | **0** | **0** |
| Effective levels after `configure_logging("DEBUG")` | `pymongo` and `pymongo.command`/`.connection`/`.serverSelection`/`.topology` WARNING (debug off); `botocore`, `urllib3`, `httpx` WARNING; `sqlglot` CRITICAL | same |
| DPS error raised for E11000 | `pk_collision` (`PkCollision`): **0 values** in its str, repr, args or cause | same |
| Raw driver errors (not DPS) | `DuplicateKeyError` text holds the key. Oracle 23ai's `IntegrityError` (ORA-00001) text **holds the duplicate value** (see N4). | same |
| **Positive control:** `pymongo` set to DEBUG by hand, bypassing the cap | **3 of 3 values seen**, so the capture works and the cap is what protects | 3 of 3 |
| oracledb | Uses no stdlib logging: no `import logging` in the package and 0 records at DEBUG. `PYO_DEBUG_PACKETS` (packet dump to stdout) is not set in the app or worker. | same |
| Lines outside structlog | 2 × asyncio `Using selector: EpollSelector` (value-free) | same |
| Scratch objects | Mongo database `s1pc_dbg_*` dropped. Oracle `CRM_MASKED.S1PC_DBG_*` dropped. | same |

## 3. LLM exposure: `dps_llm_raw_pii_sent_total` is 0 for every tenant

| Check | Result |
|---|---|
| `/metrics`, app (:8400) and worker (:9400) | `dps_llm_raw_pii_sent_total 0.0` on both. No `dps_llm_raw_pii_sent_by_tenant_total`, `dps_llm_calls_by_tenant_total` or `…exposure_blocked…` series exist (no LLM call was made). |
| Tenants (`dps_tenant` ∪ audit ∪ jobs) | `_system`, `default`, `s1m3sec-a` |
| `GET /reports/summary` per tenant (`X-DPS-Tenant`) | `default`: `raw_pii_sent_to_llm 0`, `llm_calls 0`, `llm_blocked 0`. `s1m3sec-a`: the same. `_system` is a pseudo-tenant and cannot be selected. |
| Audit rows, `counts.llm_*` summed per tenant | `default` (910 rows), `s1m3sec-a` (102 rows) and `_system` (2 rows): calls 0, raw values 0, blocked 0 |
| Jobs, `progress.metrics.llm` | 0 jobs with LLM metrics. Raw values 0 for each tenant. |
| `dps_report_daily.llm_raw_values` | `default` 0 |
| Console tiles | "Raw PII to LLM 0" on the job details, "Raw values to LLM 0" in the evidence packs, "0 raw values sent to the LLM" at discovery |

**Why 0 is trivially true on this stack:** the provider is `stub`. T3 labelling and genspec skip `stub`, and text
rewrite is off. So I ran a **mechanism control** (`llm_counter_control.py`) in its own process inside the app
container, with the stub gateway:

- `warn` mode, tenant `s1pc-probe-a`: `dps_llm_raw_pii_sent_by_tenant_total{tenant_id="s1pc-probe-a"} 1.0`.
- `strict` mode, tenant `s1pc-probe-b`: `LlmEgressBlocked`, and `…exposure_blocked_by_tenant_total{…-b} 1.0`.
- Clean prompt, tenant `s1pc-probe-c`: `calls_by_tenant 1`, raw 0.

The probe string never reached the probe's captured logs. The per-tenant counters work in the shipped image.

## 4. Console walk (private Playwright), pages touched by M3 and M5, compared with the mockups

`walk.py` drove the real stack with the demo key in `sessionStorage`. Every page was shot at 1440 dark, 390 dark
and 1440 light, next to its mockup. I recorded console errors, page errors, 4xx/5xx and failed requests, the
390 px horizontal scroll, and whether any value or key material was on the page (text and HTML).

### Phase results

| Phase | Result |
|---|---|
| **Wizard, Oracle** (06 → 03 → 10). `smoke-ora-src`, `CRM.CUSTOMERS WHERE SEGMENT = 'retail'` (9,824 rows) → new table `CRM_MASKED.S1PC_ORA_*` | PASS, 29.7 s. Test connection 2/2 with "Oracle Database 23.26.0.0". Owners CRM, LOANS. 6/6 selection checks, the literal bound. 16-cell heat-map with value shapes only. Accept. Copy to a table. Run. The key card is shown once with fields `alg, consistency_scope, created_at, dek, kcv, kid, wrap, wrapped_dek`. Job detail. Evidence pack: selection reads `"SEGMENT" = :l1 · 1 bound parameter`, verified, 0 leaks. |
| **Wizard, MongoDB** (06 → 03 → 10). `crm.profiles` with filter `{"segment":"premium"}` (3,469 documents) → `crm_uat.s1pc_mdb_*` | PASS, 22.8 s. "transactions: yes". No owner select. `$where` refused in the page before any request, not echoed. `$jsonSchema` refused by the server (`selection_operator_denied`), sentinel not echoed. Heat-map shows the 17 leaf paths. Job detail labels every leaf by its path. Evidence: selection `{"segment":"?"} · 1 bound literal` and "DPS read 3,469 documents from MongoDB". **But section 3 mislabels the leaves (B1, B2).** |
| **Job group** (07, 03): a PG + MongoDB + Oracle group built through the API under one key (20,000 rows each, 3 runs succeeded), then the console | PASS, 79 s. Group card: key node "entity-scoped: CUSTOMER_ID · EMAIL · …", wires, 3 system cards (ORA, MDB, PG). "not checked yet" changes to **Run consistency check** → verdict *passed*, **3 / 3 consistent**, 3 pairs, 0 bad. Join coverage: 3 member rows (counts only; MongoDB "prefixed · 5-char prefix"), the compared-entities table, the prefix callout. The verdict survives a reload. 3 `.grp` badges in the jobs table. "Add a job to this group" opens the wizard preset to the group. The grouped Oracle job's detail: crumb "Job group …", subtitle names the other two targets, "consistency key shared with", "Cross-system joins **3 / 3** consistent" tile, and "Open consistency check" lands on `#group`. |
| **New job group dialog** | PASS. Done stays disabled until "I have stored the key and the seed" is ticked. The `.dpskey` file has the right name and no seed. `.dpsseed` = `{group, synthesis_seed}`. The dialog shows only the kid and the seed fingerprint. Key and seed are not in local or session storage and not in the DOM after Done. |
| **Admin** (11) | PASS. KPIs: Active API keys, Tenants, Posture checks (5 of 11 pass, local by design), Keys stored in clear 0. Sections: API keys, Tenants and quotas, Registered connections (Oracle and MongoDB connections render with engine, secret ref and "who may write"), Service health, Posture. Create key `s1pc-probe-*`: raw key shown once (shape OK), "I have stored the key" hides it, not in DOM, storage or URL. The key was then **disabled** (clean-up). |
| **Reports** (09) | PASS on the re-run (`walk-reports-rerun.json`). Run 1 searched a ticket *prefix*, which the API by design does not match: it is an exact job-id/ticket/key-name match. KPIs include "Customer values stored 0". An exact search finds `CHG-S1PC-ORA` (1 pack), `CHG-S1PC-MDB` (1) and `CHG-S1PC-GRP` (3), all "passed · 0 leaks". The layer filter lives in the URL. The audit table has 50 rows. **Downloads:** audit CSV (143,621 bytes), 3 × pack JSON, 3 × pack PDF (`%PDF-`). In all of them: **0 values, 0 key material**, and 0 key shapes other than the packs' sha256 hashes. |
| **Evidence** (10), Oracle and MongoDB packs | Section headings 1–5, 7, 8 (see N6). "Key custody" row present. "DPS stored no key". The print view (A4 width) has no horizontal scroll. Download JSON and PDF: 0 values, 0 key material. MongoDB section 3 is **B1 and B2**. |
| **Policies** (05) | PASS (read-only). `/policies` redirects to `gdpr-pseudonymise`. Sections present: Presets, Treatment per entity type, Declared fields, Custom recognizer, Plan preview. Version history is absent because this preset is not adopted by the tenant, which is by design. Live recognizer test "1 match · 1 on the allow list". Re-run plan gives 11 rows. |
| **Sweep**: 17 routes (every `App.tsx` route, the new runs' job and evidence pages, the group view, the 404) × 1440 dark, 1440 light, 390 dark = **51 visits** | **0 console errors, 0 page errors, 0 HTTP ≥ 400, 0 failed requests. 0 pages with horizontal scroll. 0 pages showing a demo value. 0 pages holding key material.** |

**Console errors over the whole walk:** the only browser console errors were "Failed to load resource: … 422".
They came from deliberately invalid input (N1):

- in the walk, MongoDB `$jsonSchema` → `POST /connections/{id}/count` 422;
- in the draft probe, an Oracle WHERE with an unbound `:p`.

### Mockup comparison

There are side-by-side images in `shots/pair-*.png` and app/mockup screenshots for every page. I also extracted
each mockup's structural labels (h2, h3, card titles, KPI labels, table headings) and looked for them on the app
page:

| Page | Labels found | Comment |
|---|---|---|
| 01 dashboard | 16/16 | |
| 11 admin | 30/31 | missing: the confirm dialog's title (a dialog that was closed) |
| 09 reports | 21/24 | missing: the chart's "View as table" headings |
| 03 job detail | 9/9 for the grouped Oracle job | the MongoDB job reads "Run · partitions by _id range" and "(superseded)", which is the snapshot's real state after the group re-discovered `crm.profiles` |
| 05 policies | 19/24 | missing labels are draft- and version-specific |
| 07 jobs | 9/16 | the mockup's sample of masked join tokens is replaced by counts-only join coverage, a documented M3 deviation that is the privacy-preferring choice |
| 06 wizard | 10/40 at step 1 | the mockup HTML holds all 5 steps at once; the walk covered every step on both engines |
| 10 evidence | 14/34 | the mockup file holds an obfuscate pack and a synthetic pack; section numbering differs (N6) |

On visual inspection the group card, the grouped job detail, Admin and Reports follow their mockups closely.

## 5. Privacy properties of the console

| Property | Result |
|---|---|
| Key material shown once | Both wizard runs, the API group key, the dialog's key and seed, and the raw API key: 0 in local or session storage, 0 in any URL, 0 in the DOM after "stored". 0 in any container log or metadata row (the second scan in §1). |
| Wizard draft, contract §14 | On "Save draft" for Oracle (WHERE with a literal) and MongoDB (filter with a literal and `$exists`): `dps.jobDraft` holds **no filter literal, no operator, no bind**. A probe password typed into the New-connection form is **not** in the draft (`secretRef` and `username` are empty). It holds no key material. |
| Values on pages | 0 demo values in the text or HTML of any page: discovery, job detail, evidence, group card, Admin, Reports, Policies and the 51 sweep visits. The page corpus was 480 values (Oracle, MongoDB and PG names, e-mails, mobiles, ids). Discovery shows value shapes only. |
| Search boxes | Two demo values pasted into the Reports search and the Jobs search reached **0 container log lines and 0 audit rows**. The Jobs search filters on the client. The Reports search sends `q` to `/reports/evidence` and `/admin/audit` and keeps it in the page URL (N2). |

## 6. Source vs target (counts only) for every target this walk wrote, before they were dropped

| Target | Rows | Personal columns with a value equal to any source value |
|---|---|---|
| `CRM_MASKED.S1PC_ORA_*` (wizard, retail) | 9,824 = source 9,824 | FULL_NAME, NATIONAL_ID, TAX_ID, EMAIL, MOBILE, ADDR_LINE1, PIN_CODE, NOTES, CUSTOMER_ID: all **0** |
| `crm_uat.s1pc_mdb_*` (wizard, premium) | 3,469 = 3,469 | fullName, email, mobile, taxId, notes, addresses line1, accounts iban: **0**. customerId: 2 equal to *another* customer's id, 0 on the same `_id` |
| Group: `CRM_MASKED.CUSTOMERS_S1PC_*` | 20,000 = 20,000 | all **0** except CUSTOMER_ID 7 (another customer's id) |
| Group: `crm_uat.profiles_s1pc_*` | 20,000 = 20,000 | all **0** except customerId 7 (another customer's id), 0 on the same id |
| Group: `bank_uat.customers_s1pc_*` | 20,000 = 20,000 | all **0** except customer_id 7 (another customer's id) |

The id matches are format-preserving tokens that land on another real id in the same digit domain, with 0 fixed
points. This is known (I-m5 N9). The 7 is the same in all three engines because the tokens are consistent.

## 7. Blocking findings

### B1: the console Evidence page labels every nested MongoDB leaf by its top-level field (`frontend/src/pages/EvidencePack.tsx`)

**What happens.** For a MongoDB pack, section 3 "Classification snapshot" renders the snapshot column's `name`:
`{s?.name ?? …}` at line 184. Every MongoDB leaf shares its top-level field's `name`. The pack paths are
`$.accounts[*].iban, $.contact.addresses[*].line1, $.contact.addresses[*].pin, $.contact.email, $.contact.mobile,
$.customerId, $.kyc.taxId, $.notes, $.profile.dob, $.profile.fullName`. The page shows them as
`accounts, contact, contact, contact, contact, customerId, kyc, notes, profile, profile`.

**Why it misleads.** The "kept" note (line 196, `c.name`) reads "7 columns kept, no entity: _id, accounts,
contact, kyc, riskScore, segment, tags". It names fields whose e-mail, mobile, address, PIN, IBAN and tax-id
leaves *were* transformed. It does not name the leaves that were actually kept: `accounts[].balance`,
`contact.addresses[].type` and `kyc.verifiedAt`. So the evidence view (and its Print, "the PDF look") misstates
what was left untransformed. Nothing about the protection is wrong. What is wrong is the record of it.

**What is correct.** Job detail, the wizard, the server PDF and HTML, and the JSON all show full paths. Oracle
packs render correctly. Proof: `logs/evidence_labels_probe.json`, plus the screenshots
`06-mdb-evidence-desktop-dark.png` and `06-mdb-detail-desktop-dark.png`.

**Root cause.** M3 added `columnLabel()` (`lib/engines.ts`) and used it on the heat-map, the review, the decision
list and job detail "since every MongoDB leaf shares its top-level `name`" (M3-DEVIATIONS Task 18b). The
evidence page was missed.

**Fix.**

- Line 184: render `columnLabel(s ?? { path: String(c.path), name: String(c.path).replace(/^\$\./, '') })`.
- Line 196: use `columnLabel(c)` in both kept lists.
- Add a Vitest that renders `EvidenceSheet` with a MongoDB pack whose two leaves share `contact`. Assert
  `contact.email` and `contact.mobile` appear, and that the kept note names `contact.addresses[].type`, not
  `contact`.

### B2: a MongoDB pack has no per-leaf value counts for nested leaves (`dps/layers/connectors/evidence.py`, `build_pack`)

**What happens.** `build_pack` sets `"values": per_column.get(p.name)` (and `k.name` for pseudonyms).
`BatchTransformer._column_key` counts a nested leaf under its **path**: the job's counters are
`col:$.contact.email`, `col:$.kyc.taxId`, … and `col:customerId`, `col:notes`. So in the sealed pack, `values` is
`null` for 8 of the 10 protected leaves. The Evidence page's VALUES column then shows "—" for them. Only
`customerId` (3,469) and `notes` (6,620) have counts. The packs are sealed and hashed, so every MongoDB pack
written before the fix stays incomplete.

**Fix.** Look the count up with the same key the transformer uses:
`per_column.get(docpaths.column_key(p.path, p.name))` (and the same for `k`). Add a test that a MongoDB run's
pack has non-null `values` for `$.contact.email`.

## 8. Non-blocking notes

- **N1. Console errors on deliberate refusals.** A server-side selection refusal makes the browser log "Failed to
  load resource: … 422" on the console. This happens for a MongoDB operator outside the allow-list
  (`selection_operator_denied`) and for an Oracle WHERE with an unbound `:name`. The page shows the code and does
  not echo the input. M5 avoided the same console error on Admin hosts by checking in the browser first. A client
  mirror of the operator allow-list and the bind check would remove it. Only invalid input triggers it; the sweep
  had 0 errors.
- **N2. The Reports search term travels in URLs.** The search box puts `q` in the page URL (browser history,
  shared links) and in the GET query string to `/reports/evidence` and `/admin/audit`. DPS logs and audits
  neither: 0 hits after pasting two demo values. But an ingress or proxy access log would record a customer value
  pasted by mistake. The search only matches job ids, tickets and key names exactly, so `q` could be sent only
  when it has that shape, or not put in the URL.
- **N3 (outside this lens; for the data lens or F4). PG demo seed: one PAN and one IBAN for everyone.**
  - All 24,000 `bank.cards.pan` share one value.
  - `bank.accounts.iban` (3,011 non-null) has one value, and so does `bank.transactions.counterparty_iban`
    (10,053). The same holds in `bank_restore` and `m3e2e`.
  - Cause: `tools/seed_sources.py` passes a constant original to `engine.surrogate("CARD_PAN" | "IBAN", …,
    anchor)`, which is value-deterministic and ignores the anchor (checked).
  - This is pre-existing since `17e14a2c` (M2). MongoDB IBANs are distinct because their originals vary.
  - Effect: source-vs-target proofs on PG PAN and IBAN columns are vacuous on the demo.
- **N4. Driver errors carry values.** Oracle 23ai's ORA-00001 text includes the duplicate column value, and
  pymongo's `DuplicateKeyError` quotes the key. DPS's mapped errors hold 0 values, and DPS never turns a driver
  exception into text (the M3 AST test covers the M3 modules). Keep that rule for every module that touches a
  driver. Cheap hardening: add `("oracledb", logging.WARNING)` to `_THIRD_PARTY_LEVELS`. With
  `DPS_LOG_LEVEL=DEBUG` the `oracledb` logger is effectively DEBUG today; it is harmless only because the library
  does not log.
- **N5. Id collisions are known.** FPE id collisions (7 of 20,000 per group member, 2 of 3,469) are known (I-m5
  N9). Not a leak: 0 on the same id.
- **N6. Evidence section numbering.** An obfuscate pack's sections are numbered 1–5, 7, 8, while mockup 10 uses
  1–7. This is a documented M5 deviation ("the M2 sheet numbers sections 1–8 for every pack").
- **N7. Cosmetic layout.**
  - The wizard's "Review · every column" table is wider than its card at 1440 px for every engine, PostgreSQL
    included: 949 px in a 710 px `.tbl-scroll`. It scrolls inside the card. Pre-existing (M2).
  - The Jobs page lists up to 200 jobs above the group section (an 18,362 px page at 1440 px). The `#group` anchor
    and "Open consistency check" land on it.

## 9. State left on the shared stack

- **Dropped:** the five targets this walk wrote, and the scratch objects of the driver probes.
- **Left:** the evidence packs and jobs of the runs, which is normal run history.
- **Left:** the groups `s1pc-grp-*` and `s1pc-dialog-*`.
- **Left:** one API key `s1pc-probe-*`, **disabled**.

## 10. Files

All under `~/.cache/dps-build/reports/S1-M3-privacy-console-r1/`.

**Scripts:**

- `common.py`, `walk.py`, `scan.py`
- `driver_debug_probe.py`, `llm_counter_control.py`, `metrics_scan.py`, `secret_ctx.py`
- `evidence_labels_probe.py`, `draft_probe.py`, `draft_probe2.py`, `search_probe.py`, `overflow_probe.py`

**Results in `logs/`:**

- Walk: `walk-run1.json` (all phases plus the sweep), `walk-reports-rerun.json`
- Scans: `scan-baseline.json`, `scan-s1pc_252c9e.json` (with the walk's key material), `scan-s1pc_0118d9.json`,
  `scan-final.json`
- Driver probes: `driver_debug_probe.json`, `driver_debug_probe_worker.json`
- Other probes: `llm_counter_control.json`, `metrics_scan.json`, `evidence_labels_probe.json`,
  `draft_probe*.json`, `search_probe.json`, `overflow_probe.json`

**Screenshots in `shots/`:** 173 app and mockup shots, plus `pair-*.png` side-by-side comparisons.
