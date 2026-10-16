# S1-M3: post-merge verification of main after the M3 merge (lens "privacy-console", round 2)

**Verdict: PASS.** There are 0 blocking findings.

Both round-1 blockers are fixed and verified on fresh packs:

- **r1 B1:** the Evidence page now labels every MongoDB leaf by its path.
- **r1 B2:** a MongoDB pack now counts every nested leaf.

Everything else in this lens is green:

- **DPS logs:** 0 demo values, 0 first names and 0 key material in DPS's own logs. This includes the pymongo,
  oracledb and botocore paths at DEBUG, run in the app and in the worker.
- **LLM exposure:** raw PII sent to the LLM is 0 for every tenant, and the per-tenant counters work.
- **Console:** 0 console errors except deliberate 422 refusals. No horizontal scroll at 390 px. No value or key
  material on any page, in storage or in any URL.
- **Data:** row-aligned survivors are 0 for all 7 targets the walk wrote.

There are four new non-blocking notes:

- **R2-N1:** one demo value sits in **source-pg's own PostgreSQL server log**. It is PostgreSQL's ERROR line for a
  failing cast in a DPS count query. The security-r2 verifier sent that query on purpose as an error-based probe.
  DPS answered 502 without the value.
- **R2-N2:** the Evidence page's "Seed" sub-line says "Data key … key id" on synthesize packs.
- **R2-N3:** long MongoDB paths overlap the Type cell on the Evidence page at 390 px.
- **R2-N4 (instrument):** a full `docker logs` of the mongo container stops at 11:10 UTC. Round 1 and this round's
  full scans therefore missed the mongod log since the Docker restart. I scanned that segment separately and it has
  0 values.

| | |
|---|---|
| Date | 2026-10-14, 21:40–22:20 IST |
| Tree verified | `main` @ `5c11e49` (S1-M3 fix round 1), clean at start and end; no commit landed during the run |
| Image = HEAD | sha256 of all 175 tracked files under `dps/` and `tools/` in `app` equals the checkout (0 diffs); the served console bundle `index-DsbmQBlD.js` / `index-BCtPdVwe.css` is byte-identical to the checkout's HEAD build and carries the fix commit's new texts |
| Stack | compose project `data-privacy-services`, default ports, left running by the gates verifier (image `c18bced81712`, app/worker recreated 15:23 UTC). Not restarted, rebuilt or brought down. tokenizer-mock on 8430. The engines-r2 (`VER-S1M3-ENG-R2`) and security-r2 (`s1m3secr2-*`) verifiers worked on the same stack concurrently. |
| Browser | private Playwright `playwright==1.55.0` (Chromium 1187), venv `~/.cache/dps-build/pw-s1m3-privacy-console` (this stream's venv from r1; also httpx, asyncpg, pymongo, oracledb, pyahocorasick, pypdf, pillow). Never the shared Playwright MCP browser. |
| Probes, logs, shots | `~/.cache/dps-build/reports/S1-M3-privacy-console-r2/` (scripts at the top level, JSON/TXT in `logs/`, 249 images in `shots/`) |
| Repository files modified | **none** (`git status` clean before and after; the tracked-shot console tests were not run) |

## 1. Round-1 blockers re-verified

| r1 finding | Check on fresh packs (this walk) | Result |
|---|---|---|
| **B1:** the Evidence page labels nested MongoDB leaves by their top-level field | Section 3 labels compared with `columnLabel(path)` of every pack column. The kept-note list was compared with the snapshot's kept leaves. Run on 3 MongoDB packs: wizard obfuscate (10 columns, 8 nested), wizard synthesize (11 columns, 8 nested) and the group's MongoDB member (10 columns, 8 nested). | **Fixed.** `labels_match_paths` holds on all 3 packs, with 0 duplicate labels. The kept note reads, for example, "7 columns kept, no entity: _id, accounts[].balance, contact.addresses[].type, kyc.verifiedAt, riskScore, segment, tags[]", which matches the snapshot exactly. The Oracle packs match too: wizard obfuscate 10 columns, group member 10, synthesize 11 (`evidence_labels_probe.json`). |
| **B2:** a MongoDB pack has `values: null` for nested leaves | Pack JSON `values` per column. Page VALUES cells. Server PDF and HTML. | **Fixed.** 0 null `values` in every pack. The page shows a number in every row (10/10, 11/11, 10/10) and 0 dashes. The server PDF and the HTML artefact list every leaf path (10/10, 11/11), from `pdf_labels_probe.json`. |

Screenshots: `10-mdb-evidence-section3-*`, `10-mdbsyn-evidence-section3-*`, `10-group-mdb-evidence-*` and
`crop-mdb-evidence-s3.png`.

## 2. Log scans: every main container, demo values, first names and key material (counts only)

**Corpus (memory only).**

- 1,520,692 distinct values of 8 or more characters, from 80 high-cardinality columns:
  - PostgreSQL `bank.*`: 45 columns, 996,588 values.
  - Oracle `CRM.*`/`LOANS.*`: 25 columns, 352,695 values.
  - MongoDB `crm.profiles`, every leaf path: 10 paths, 171,409 values.
- Each value is also searched in its JSON-escaped spelling. Matching is Aho-Corasick on whole tokens.
- **New in r2:** 865 first names (6 or more letters, whole word, case-sensitive), taken from all three engines'
  full names. This covers the r1 engines B2 class: names in free text.

**Needles.**

- The secret-named environment values of all 13 containers.
- Every file and JSON leaf under the app's `DPS_SECRET_DIR`.
- The bootstrap key and the KEK literal, both raw and decoded.
- Key-shaped regexes.
- The post-walk scan also had the walk's **12 key-material needles**: 2 job keys × (dek, wrapped_dek), 2
  synthesis seeds, the API group key, the dialog's `.dpskey` and `.dpsseed`, and the raw API key.
- The final scan also had the probes' own literals: the refusal sentinel, a probe password typed into the
  New-connection form, and the LLM-control probe string.

**Masking.** A hit's context is now stored with every letter and digit masked. Only JSON keys are kept. In r1, short
words around a hit were kept.

**Targets.**

- `docker logs` (stdout and stderr) of every container of the project.
- All 18 `dps.*` metadata tables (4,815 rows at the end).
- **New in r2: the app's artefact store** at `/app/var/artefacts`: 514 files at the end (231 pack HTML, 274 JSON,
  9 PDF), read with `tar` and the PDFs text-extracted.
- `/metrics` and `/readyz` of the app and the worker.

There were three scans: the baseline at 21:44, the post-walk scan with the key material at 22:04, and the final scan
at 22:12 IST. The table shows the final scan:

| Container | Log bytes | Values | First names | Secret needles | Key shapes |
|---|---|---|---|---|---|
| app | 652,031 | **0** | 0 | 0 | 0 |
| worker | 150,212 | **0** | 0 | 0 | 0 |
| tokenizer-mock / azure-ocr-mock | 12,151 / 41,999 | **0** | 0 | 0 | 0 |
| migrate / seed-sources / minio-init / demo-secrets | small | **0** | 0 | 0 | 0 |
| oracle | 124,783 | **0** | 0 | 0 | 0 |
| mongo, full `docker logs` (pre-restart segment, see R2-N4) | 22,582,315 | **0** | 0 | 0 | 0 |
| **mongo, segment since the restart** (`--tail 147,250`, 11:42:17 → 16:45 UTC) | 51,261,662 | **0** outside connection UUIDs (4 raw hits = all-digit first groups of `$uuid` connection ids, checked by context) | 0 | 0 | 0 |
| dps-db | 178,227 | **0** | 0 | 41 (third-party lines) | 0 |
| source-pg | 45,299 | **1 (R2-N1)** | 0 | 83 (third-party lines) | 0 |
| minio | 1,158 | **0** | 0 | 4 (MinIO's own default-credentials warning) | 0 |

**Secret-needle hits.** All of them are third-party servers' own lines, the same as in r1. Each was checked by
masked context (`logs/secret_ctx-final.txt`):

- `password authentication failed for user "<name>"`: the demo logins have username equal to password.
- The word `postgres` in `/var/lib/postgresql` paths and in "owned by user" lines.
- MinIO's startup warning about its default credentials.

The 12 walk needles and the 3 probe literals are found **0 times anywhere**: no log, no metadata row, no artefact.

**Metadata rows.**

- 0 values, 0 first names and 0 key material in `dps_audit_log` (2,469 rows), `dps_evidence` (290), `dps_job`
  (550), `dps_snapshot`, `dps_job_group`, `dps_artefact`, `dps_connection`, `dps_api_key`, and the policy, report,
  tenant and worker tables.
- `dps_job_partition` matches 7 values. These are the encoded `last_key` resume marks, which contract §4.1 stores
  by design. Their masked context is `"last_key":[{"t": …, "v": "[HIT]"}]`.
- The only "secret" hit in rows and artefacts is the word `postgres`, which is also the local superuser password.
  Every occurrence is `"engine": "postgres"` or the HTML heading "postgres bank. …" (`logs/rows_secret_ctx.txt`).
- The only hex-256 shapes are the sha256 hashes.

**Artefact store.** 0 values, 0 first names and 0 key material in all 514 files.

**`/metrics` and `/readyz`.**

- 0 values.
- The only "secret" match is a 6-character demo login (username equal to password). It is the prefix of the metric
  name `dps_rows_written_total`, with 0 whole-token matches (`logs/metrics_ctx.txt`).
- The 2 `dps_` key shapes are the declared metric names `dps_llm_raw_pii_sent_by_tenant_total` and
  `dps_llm_exposure_blocked_by_tenant_total`.
- Label names are value-free.

**Positive controls.** These were planted into a copy of the app log, every scan: values found 2/2, secret needles
2/2, first names 2/2. In the mongod segment-2 scan, the control value was found 1/1.

**DPS's own warnings** (shapes only). There are 7 app warnings: 5 × `auth_rejected` (probes) and
2 × `oracle_call_failed driver_code=ORA-##### op=count`, which is value-free and carries the code only. There are
0 tracebacks in the app or the worker.

## 3. Driver loggers at DEBUG (pymongo, oracledb, botocore), in the app and the worker containers

`driver_debug_probe.py` ran as its own process via `docker exec -i`, so the running services were untouched:

- Setup: `configure_logging("DEBUG")`, which is exactly `DPS_LOG_LEVEL=DEBUG`; a root handler at DEBUG; and
  `oracledb` forced to DEBUG.
- Workload, the same as r1: the MongoDB and Oracle connectors' describe, count, sample and read_partition; the DPS
  table writer forced into E11000 on a real `customerId`; raw pymongo; raw oracledb with ORA-00001 on a real value.
- **Extended for the r1 fixes B3/B4.** The DPS `FilesWriter` uploaded 120 real source rows per engine × jsonl, csv
  and parquet to MinIO. This used DPS's `S3ObjectStore` with the `exports-s3` connection's own secret ref. MongoDB
  had 5 document columns. Oracle FF9 timestamps were written as text.

| | App | Worker |
|---|---|---|
| Demo values held (memory) | 1,813 | 1,829 |
| **Values in the captured output outside the controls** | **0** (60 bytes = 2 × asyncio "Using selector") | **0** |
| Effective levels after `configure_logging("DEBUG")` | `pymongo` (+ command/connection/serverSelection/topology), `botocore`, `boto3`, `s3transfer`, `urllib3`, `httpx` at WARNING; `sqlglot` at CRITICAL; `oracledb` at DEBUG (r1 N4, still not capped) | same |
| Files writer at DEBUG | 6/6 uploads of 120 rows. MongoDB had 5 document columns. Objects deleted afterwards, 0 left. | same |
| pymongo control (cap lifted by hand) | **3 of 3 values seen**, so the capture works and the cap is what protects | 3 of 3 |
| botocore control (cap lifted, raw put with a value in the body) | botocore emitted 17,494 bytes of DEBUG output, but this botocore version **does not print the put body**, so the control could not show a leak. The DPS cap keeps all of it out, which is 0 bytes in the verdict window. | same |
| DPS error for E11000 | `pk_collision`, with 0 values in its text | same |
| Raw driver errors (not DPS) | pymongo `DuplicateKeyError` and Oracle 23ai `IntegrityError` text each hold the value (r1 N4, known; DPS never stringifies them) | same |
| oracledb | No stdlib logging: 0 records at DEBUG. `PYO_*` is unset. | same |
| Scratch | Mongo `s1pc2_dbg_*` dropped. `CRM_MASKED.S1PC2_DBG_*` dropped. MinIO `s1pc2-dbg/` has 0 objects left. | same |

## 4. LLM exposure: `dps_llm_raw_pii_sent_total` is 0 for every tenant

| Check | Result |
|---|---|
| `/metrics` on the app (:8400) and the worker (:9400) | `dps_llm_raw_pii_sent_total 0.0` on both. No per-tenant series exists, because no LLM call was made. |
| Tenants (`dps_tenant` ∪ audit ∪ jobs) | `_system`, `default`, `s1m3sec-a`, `s1m3secr2-a` |
| `GET /reports/summary` per tenant | `default`, `s1m3sec-a` and `s1m3secr2-a`: `raw_pii_sent_to_llm 0`, `llm_calls 0`, `llm_blocked 0`. `_system` is a pseudo-tenant. |
| Audit `counts.llm_*` per tenant | `default` (2,255 rows), `s1m3sec-a` (102), `s1m3secr2-a` (110), `_system` (2): calls 0, raw values 0, blocked 0 |
| Jobs `progress.metrics.llm` | 0 jobs carry LLM metrics. Raw values 0 per tenant. |
| `dps_report_daily.llm_raw_values` | 0 for each of the 3 tenants |
| Console tiles | "Raw values to LLM 0" on every pack, "Raw PII to LLM 0" on job details |
| **Mechanism control** (`llm_counter_control.py`, own process in the app, stub gateway) | `warn`, tenant `s1pc2-probe-a`: `dps_llm_raw_pii_sent_by_tenant_total{tenant_id="s1pc2-probe-a"} 1.0`. `strict`, tenant `s1pc2-probe-b`: `LlmEgressBlocked` and `…exposure_blocked_by_tenant_total 1.0`. Clean prompt, tenant `s1pc2-probe-c`: calls 1, raw 0. The probe string never reached the captured logs or any container log. |

## 5. Console walk (private Playwright), pages touched by M3 and M5, compared with the mockups

`walk.py` (tag `s1pc2_68a893`) drove the real stack. Every page was shot at 1440 dark, 390 dark and 1440 light.
Each phase recorded:

- console errors, page errors, HTTP ≥ 400 and failed requests;
- horizontal scroll at 1440 and at 390;
- whether a demo value was on the page (text and HTML, 480-value page corpus);
- whether key material was in the DOM, storage or URL.

The ORA phase took 443 s because it queued behind about 20 engines-r2 jobs on 4 worker slots.

### Phase results

| Phase | Result |
|---|---|
| **Wizard, Oracle** (06 → 03 → 10): `smoke-ora-src`, `CRM.CUSTOMERS WHERE SEGMENT = 'retail'` (9,824 rows) → `CRM_MASKED.S1PC2_ORA_*` | PASS. Test connection banner "Oracle Database 23…". Owners CRM, LOANS. 16-cell heat-map. Key card shown once: `alg, consistency_scope, created_at, dek, kcv, kid, wrap, wrapped_dek`. 0 key material in the DOM after "stored", in storage or in the URL. Evidence: selection `"SEGMENT" = :l1 · 1 bound parameter`, pack passed, 0 hits, 103,133 values checked. |
| **Wizard, MongoDB**: `crm.profiles` with filter `{"segment":"premium"}` (3,469 documents) → `crm_uat.s1pc2_mdb_*` | PASS, 25 s. "transactions: yes". No owner and no schema field. `$where` refused on the page, not echoed. `$jsonSchema` refused by the server (422 `selection_operator_denied`), sentinel not echoed. Heat-map shows all 17 leaf paths. Evidence selection `{"segment":"?"} · 1 bound literal`. "DPS read 3,469 documents from MongoDB". **B1/B2 fixed (§1).** |
| **Wizard, Oracle synthesize (new in r2)**: `CRM.CUSTOMERS WHERE SEGMENT = 'premium'` (3,469) → `CRM_MASKED.S1PC2_ORASYN_*` | PASS, 21 s. Step 5 "Synthesis seed" card. Seed shown once (`alg, created_at, seed`), not in the DOM after "stored", not in storage or the URL. Evidence has sections 1–8 including **6 "Synthetic fidelity and privacy"**, and states "The synthetic output has no key; DPS kept no seed". CUSTOMER_ID is `key_pseudonym`. Labels and values are correct. |
| **Wizard, MongoDB synthesize (new)**: filter `{"segment":"retail"}` (9,824) → `crm_uat.s1pc2_mdbsyn_*` | PASS, 31 s. Seed shown once. Section 6 present. `_id` (objectId) and `customerId` are `key_pseudonym`. 11 leaves labelled by path with counts. |
| **Job group** (07, 03): PG + MongoDB + Oracle, built through the API under one key, 20,000 rows each, 3/3 succeeded | PASS, 94 s. Card: key node "entity-scoped: CUSTOMER_ID · EMAIL · …", wires, 3 `.sys` cards (ORA/MDB/PG). "not checked yet" changes to **Run consistency check** → *passed*, **3 / 3 consistent**, 3 pairs, 0 bad. Join coverage has 3 member rows, counts only ("counts only · no value is shown"), and the 5-char-prefix callout. The verdict survives a reload. 3 `.grp` badges. "Add a job to this group" opens the wizard with the group preset. The grouped Oracle job's detail has: crumb, subtitle naming both other targets, "consistency key shared with", "Cross-system joins 3 / 3 consistent", and "Open consistency check" → `#group`. **The group's MongoDB member pack: B1/B2 fixed.** |
| **New job group dialog** | PASS. Done stays disabled until "stored" is ticked. The `.dpskey` file has the right name and no seed. `.dpsseed` = `{group, synthesis_seed}`. Key and seed are not in storage, and not in the DOM after Done. |
| **Admin** (11) | PASS. KPIs: Active API keys, Tenants, Posture checks 5 of 11 (local by design), Keys stored in clear 0. All sections present. Registered connections lists Oracle/MongoDB/PG/S3 with the fix-round wording. Create key `s1pc2-probe-*`: shown once (shape OK), hidden after "stored", not in storage or URL; then **disabled**. |
| **Reports** (09) | PASS. KPIs include "Customer values stored 0". Exact search finds all 5 tickets (ORA, MDB, ORASYN, MDBSYN, GRP ×3), all "passed · 0 leaks". The layer filter lives in the URL. The audit table has 50 rows. **Downloads:** audit CSV (338,339 bytes), 5 × pack JSON and 5 × pack PDF (`%PDF-`), with 0 values, 0 key material and no key shapes besides the sha256 hashes. |
| **Evidence** (10), 5 packs (ora, mdb, orasyn, mdbsyn, group MongoDB) | PASS. Headings 1–5, 7, 8 for obfuscate packs and 1–8 for synthesize packs (r1 N6). "Key custody" row present. "DPS stored no key". The print view (794 px) has no horizontal scroll. Download JSON/PDF: 0 values, 0 key material. |
| **Policies** (05) | PASS (read-only). `/policies` → `/policies/gdpr-pseudonymise`. Sections present. Live recognizer test "1 match · 1 on the allow list". The plan has 11 rows. Version history is absent for a non-adopted preset, which is by design. |
| **Sweep**: 24 routes (every `App.tsx` route plus `/policies/gdpr-pseudonymise`, this walk's 6 job pages and 5 evidence pages, the group view, the 404) × 1440 dark, 1440 light, 390 dark = **72 visits** | **0 console errors, 0 page errors, 0 HTTP ≥ 400, 0 failed requests. 0 pages with horizontal scroll. 0 pages showing a demo value. 0 pages holding key material.** |

**Console errors over every browser run in this round.** The only console errors were "Failed to load resource:
… 422". Both came from deliberately invalid input (r1 N1, unchanged):

- the MongoDB `$jsonSchema` refusal in the walk;
- the Oracle WHERE with an unbound `:p` in the draft probe.

Other runs had 0: `draft_probe2/3`, `search_probe`, `overflow_probe`, `group_shot` and the evidence label probes.

### Mockup comparison

There are side-by-side images in `shots/pair-*.png`. They cover 01, 03 grouped detail, 05, 06 (Oracle discover and
MongoDB select), 07 group card (element shot against the mockup's group section), 09, 10 (MongoDB and Oracle
synthesize), 11, and two phone pairs.

Label coverage (the mockup's h2/h3/card titles/KPI labels/table headings found on the app page, 1440 dark):

| Page | Labels found | Comment |
|---|---|---|
| 01 dashboard | 16/16 | |
| 11 admin | 30/31 | missing only the closed confirm dialog's title |
| 09 reports | 21/24 | missing the chart's "View as table" headings |
| 07 jobs and the `?group=` view | 9/16 | The mockup's masked-token join sample is replaced by counts-only join coverage. This is a documented M3 deviation and the privacy-preferring choice. |
| 03 job detail (6 jobs) | 7–8/9 | The card title reads "Column classification (superseded)", which is the snapshot's real state after the concurrent verifiers re-discovered the same objects. MongoDB jobs partition "by _id range". |
| 06 wizard | 10/40 at step 1 | The mockup holds all 5 steps at once. Every step was walked on both engines and both modes. |
| 05 policies | 19/24 | missing draft- and version-specific labels |
| 10 evidence (5 packs) | 14/34 | The mockup holds an obfuscate pack and a synthetic pack, with different numbering (r1 N6). |

On visual inspection, the group card, the grouped job detail, the evidence sheet, Admin and Reports follow their
mockups closely. The print view wraps long MongoDB paths inside the Column cell. The 390 px layout has the R2-N3
overlap.

### Privacy properties of the console

| Property | Result |
|---|---|
| Key material shown once | Covered: 2 job keys, 2 synthesis seeds, the API group key, the dialog's key and seed, and the raw API key. All are 0 in local or session storage, 0 in any URL, 0 in the DOM after "stored", and 0 in any log, metadata row or artefact (post-walk scan with the 12 needles). |
| Wizard draft (contract §14) | "Save draft" with an Oracle WHERE (literal plus unbound bind) and a MongoDB filter (literal plus `$exists`): `dps.jobDraft` holds no literal, no operator and no bind. `"segment"` appears only as a field name in the column list. The draft's only "secret"-ish word is `credMode: "secret_ref"`, a mode label. A probe password typed into New connection is **not** in the draft; `secretRef` and `username` are empty. |
| Search boxes | Two demo values pasted into the Reports and Jobs searches reached 0 container log lines and 0 audit rows, and are not in storage. The Reports URL holds the value URL-encoded, and the GET query strings to `/reports/evidence` and `/admin/audit` carry it. This is r1 N2, unchanged. The Jobs search stays on the client. |
| Policies recognizer test (M5, API) | A demo e-mail in the sample text returns offsets only and is not echoed. The 422 paths (oversized, invalid spec, bad type) do not echo it either (`hide_input_in_errors`). 0 in logs and 0 in audit (`policies_probe.json`). |
| Server-rendered packs | The MongoDB pack PDF and HTML list every leaf path. 0 values and 0 key material in all downloads. |

## 6. Source vs target (counts only) for the 7 targets the walk wrote, before they were dropped

| Target | Rows | Result |
|---|---|---|
| `CRM_MASKED.S1PC2_ORA_*` (wizard, retail) | 9,824 = 9,824 | Every personal column has **0** values equal to any source value. **First name kept in notes, row-aligned on the FF9 `LAST_CONTACT_AT`: 0/9,824** (control: 9,810 source notes hold the row's own first name). |
| `crm_uat.s1pc2_mdb_*` (wizard, premium) | 3,469 = 3,469 | All **0**, by any source value and by same `_id`. First name in notes **0/3,469** (control 3,466). |
| Group `CRM_MASKED.CUSTOMERS_S1PC2_*`, `crm_uat.profiles_s1pc2_*`, `bank_uat.customers_s1pc2_*` | 20,000 each | All **0**, except the customer id: **5** equal *another* customer's id in each engine (FPE cross-row collisions, the same 5 because the tokens are consistent). 0 on the same id (MongoDB). First name in notes **0/20,000** for Oracle and MongoDB (controls 19,972). |
| `CRM_MASKED.S1PC2_ORASYN_*` (synthesize, premium) | 3,469 = 3,469, 3,469 aligned on FF9 | **row_same 0** for all 9 personal columns. CUSTOMER_ID **3,469/3,469 in the pseudonym band**, 0 source ids. First name in notes **0** (control 3,466). Source e-mail in notes 0. |
| `crm_uat.s1pc2_mdbsyn_*` (synthesize, retail) | 9,824 = 9,824, 9,824 aligned on the kept balance | **row_same 0** for all 7 paths. `_id` equal to a source `_id`: 0. customerId equal to any source id: 0. First name in notes **0** (control 9,810). |

**Information for the engines lens.** Some synthesize values equal *another* customer's value: Oracle FULL_NAME 141,
MOBILE 376, PIN_CODE 45; MongoDB mobile 1,124, dob 6,830, fullName 428, line1 7. Row-aligned equality is 0 in every
case. These are surrogate-pool collisions. The demo sources were generated with the same surrogate generators: 18,240
distinct mobiles in 20,000 rows implies a phone space of about 10⁵. So "any source value" proofs on the demo are
inflated. This is not a leak.

## 7. Blocking findings

**None.**

## 8. Non-blocking notes

### R2-N1 (new): a failing cast makes the source PostgreSQL server log one row value

**What happened.**

- At 16:26:51.518 UTC, source-pg logged `ERROR:  invalid input syntax for type integer: "<a demo e-mail>"`.
- The logged statement was `SELECT count(*) FROM "<schema>"."<table>" AS t WHERE (CAST("<col>" AS INT) > $1)`.
  This is DPS's count query.
- In the audit log, `connection.count` answered **502** at 16:26:51.520 for the key `s1m3secr2-x4b5d19-jobs-default`.
- The security-r2 verifier sent this deliberately: `probe_main.py` `echo_and_errors`, case `pg_cast_email`,
  `CAST(email AS integer) > 0`. That probe checks that the API response holds no value, and DPS's response did not.
- Evidence: `logs/pglog_ctx-source-pg.txt` (masked).

**What this means.** The value is in PostgreSQL's own server log, not in any DPS log, response, audit row or metadata
row. It was not there at the 21:44 baseline. The selection validator allows casting a column, which is legitimate for
`CAST(created_at AS DATE)`. On a text column with non-numeric data, PostgreSQL quotes the offending row value in its
ERROR line. DPS cannot suppress that from a non-superuser session: `log_min_messages` and `log_min_error_statement`
are superuser-only.

**Other engines.** The same probe's Oracle cases, `TO_NUMBER(EMAIL)` and `TO_DATE(FULL_NAME, …)`, put no value in
the Oracle container log. ORA-01722/ORA-01841 do not quote the value, and DPS logged only `driver_code=ORA-#####`.
MongoDB filters cannot convert types, because `$expr` is refused.

**Why it does not block.**

- It needs a malformed or adversarial selection from a `jobs`-scope key.
- No value reaches the caller or DPS.
- It is pre-existing since M2, when PostgreSQL selections began allowing CAST; it was not introduced by M3.

**Recommendation (F1/security):** refuse casts of a text-typed column to a non-text type in PostgreSQL selections.
DPS knows the column types from the shape. Alternatively, document in HANDOFF/README that a selection which fails on a
row makes PostgreSQL quote that value in its server log, so source-DB log retention applies.

### R2-N2 (new, wording; pre-existing since M2 `132dca9`): the Evidence page "Seed" sub-line on synthesize packs

**What the page says.** Section 2's `Seed` row prints "Data key returned at job creation; DPS stored no key. This pack
holds the key id only." on every synthesize pack.

**Why it is wrong.**

- The next row, Key custody, says correctly: "Synthesis seed returned … This pack holds the seed fingerprint only."
- The summary says "The synthetic output has no key; DPS kept no seed".
- Mockup 10 reads: "It is not a reveal key: generation is one-way. This pack holds no seed."

**Fix.** In `frontend/src/pages/EvidencePack.tsx` (the `synth ? 'Seed' : 'Key id'` row), make the `s2` text
mode-aware, for example: "Synthesis seed returned at job creation; DPS stored no seed. This pack holds the seed
fingerprint only."

Shot: `crop-orasyn-s6.png`.

### R2-N3 (new, cosmetic; exposed by the B1 fix): long MongoDB paths overlap the Type cell at 390 px

**What happens.** At phone width, section 3 uses the stacked three-column grid (`styles/evidence.css`,
`table.stack.c3`). A long unbroken monospace path such as `contact.addresses[].line1` overflows its cell and is drawn
over "string". "STREET_ADDRESS" is clipped to "STREET_ADDRES". There is no horizontal scroll. Desktop and the print
view are fine: print has `.snap .m { word-break: break-word }`.

**Fix.** Add `& table.stack td.m { overflow-wrap: anywhere; }`, or span the Column cell over the full row, in the
phone block.

Shot: `zoom-ev-phone.png`, `pair-phone-group-evidence.png`.

### R2-N4 (instrument; for every lens that scans logs): `docker logs` of the mongo container is truncated

**What happens.** A full `docker logs data-privacy-services-mongo-1`, with or without `--since`, returns only
08:06:46 → 11:10:14 UTC. That is 22,582,315 bytes, the same byte count r1 reported. The read stops at a truncated
record written when Docker Desktop restarted. mongod has logged to stdout since 11:42:17, and that segment (51 MB) is
returned only by `docker logs --tail N` with N ≤ ~147,000 lines.

**Consequence.** r1's "0 values in the 22.6 MB mongod log" and the full scans of this round covered only the
pre-restart segment. **This round scanned segment 2 separately: 0 values**, plus 0 first names, 0 needles and 0 key
shapes. Its 114 "Slow query" lines hold no demo value.

**Recommendation.** Scanners should read the mongo container with `--tail`, and check that the last line of any
container's log is recent.

### Round-1 notes, status

- **N1** (422 console error on deliberate refusals): unchanged.
- **N2** (the Reports search term in the URL and in GET query strings): unchanged. Still 0 in logs and audit.
- **N4** (`oracledb` not in `_THIRD_PARTY_LEVELS`): unchanged. Still harmless, with 0 records at DEBUG.
- **N5** (FPE id collisions, 5 per 20,000): unchanged.
- **N6** (section numbering): unchanged.
- **N7** (the wizard review table is wider than its card, 912–934 px in 710 px, scrolling inside the card; the Jobs
  page lists 200 jobs above the group section): unchanged (`overflow_probe.json`).
- **N3** (PG demo PAN/IBAN constant) is for the data lens and was not re-checked.

## 9. State left on the shared stack

- **Dropped:** the 7 targets this walk wrote, the driver probes' MongoDB database, Oracle table and MinIO objects.
  I verified 0 `s1pc2` objects remain in Oracle, MongoDB, PostgreSQL and MinIO.
- **Left:** the jobs and evidence packs of the 9 runs, which is normal run history.
- **Left:** the groups `s1pc2-grp-68a893` and `s1pc2-dialog-68a893`.
- **Left:** one API key `s1pc2-probe-68a893`, **disabled**.

Run ids:

| Run | Job | Evidence |
|---|---|---|
| ora | `j_01M4XKEJM4A03KSH7T24Y9GA8W` | `ev_01M4XKTYKEMP2AC1N2PRKAF6DT` |
| mdb | `j_01M4XKVEKZPX3ZCG2MXWZH9ZG7` | `ev_01M4XKVM2V0N5Q2C5CBPCB6YYM` |
| orasyn | `j_01M4XKW1H4FK0D5CP8SYVZ2M3H` | `ev_01M4XKW8SBW7B33FJ1EYKZ5FD7` |
| mdbsyn | `j_01M4XKWP8NNNXPWT4HX9DPZFZH` | `ev_01M4XKX6A9A0WZJG3CXPRDPH1M` |
| group (pg / mongo / oracle) | `j_01M4XKXGENV4HV1MJYQP72AJ39` / `j_01M4XKXMCV02GZ81KEBZKGNJ1D` / `j_01M4XKXT9M343DQYQJ5P8S2TQD` | group MongoDB pack `ev_01M4XKZF7FHDTGHHA5FPV9FJ7F` |

## 10. Files

All under `~/.cache/dps-build/reports/S1-M3-privacy-console-r2/`.

**Scripts.**

- r1 scripts with r2 additions: `common.py`, `walk.py` (adds synthesize phases, the B1/B2 checks and the group
  MongoDB pack), and `scan.py` (adds first names, the artefact store, synthesize and first-name source-vs-target,
  and masked contexts).
- `driver_debug_probe.py` (adds the files writer and the botocore control).
- `llm_counter_control.py`, `metrics_scan.py`, `metrics_ctx.py`, `secret_ctx.py`, `rows_secret_ctx.py`,
  `pglog_ctx.py`.
- `mongo_seg2_scan.py`, `selfscan.py`.
- `draft_probe.py`, `draft_probe2.py`, `draft_probe3.py`, `search_probe.py`, `policies_probe.py`,
  `pdf_labels_probe.py`, `overflow_probe.py`, `group_shot.py`, `pairs.py`.

**Results in `logs/`.**

- Walk: `walk.json`, `walk.out`.
- Scans: `scan-baseline.json`, `scan-s1pc2_68a893.json` (with the walk's key material), `scan-final.json`,
  `mongo_seg2_scan.json`.
- Driver probes: `driver_debug_probe.json`, `driver_debug_probe_worker.json`.
- Other probes: `llm_counter_control.json`, `metrics_scan.json`, `draft_probe*.json`, `search_probe.json`,
  `policies_probe.json`, `pdf_labels_probe.json`, `overflow_probe.json`.
- Masked contexts: `pglog_ctx-source-pg.txt`, `secret_ctx-*.txt`, `rows_secret_ctx.txt`, `metrics_ctx.txt`.
- `selfscan.py` over this folder: 0 values and 0 first names in any output. The only needle-like hits are field names
  and the word `postgres`.

**Screenshots in `shots/`.** 249 images: app (3 views per page), mockups, `pair-*.png` and crops.
