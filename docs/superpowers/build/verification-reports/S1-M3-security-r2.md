# S1-M3: post-merge verification of main after the M3 merge (security lens, round 2)

**Verdict: PASS.** No blocking finding in this lens. Round 1's B1 is fixed on every engine and write mode:
- `table` and `files` writes into a production-tagged connection now need `admin`;
- `POST /jobs/{id}/retry` re-checks `admin`.

Everything else in the lens still holds on Oracle and MongoDB through the live API:
- SEC-2: tenant-scoped secret refs, on the API path and the worker path.
- SEC-3: an endpoint or production change needs credentials or `admin`.
- M3-B6: column re-mapping is refused, including round 2's new variants.
- The MongoDB filter allow-list holds.
- Audit rows, logs and job rows hold no values.

The non-blocking notes are in §4. Three need follow-up elsewhere:
- **N1.** The untagged-clone bypass of the production gate is still open on main. S3 auth-tenancy F1 owns it; the
  fix is `f112044` on `fsec`, not yet merged.
- **N2.** The fsec fix ties "production" to a connection's `secret_ref`. A `pvc` connection has none, so a `pvc`
  clone of a production export path stays open even after that merge.
- **N3.** Cancel, pause and resume of an admin's production run need only the `jobs` scope.

| | |
|---|---|
| Date | 2026-10-14, 21:40–22:10 IST. Live probe window: 16:26:19Z to about 16:40Z. |
| Tree verified | `main` @ `5c11e49`, clean before and after. It contains the M3 merge `b878c50` (ancestor checked) and the S1-M3 fix round 1, the only commit since round 1. |
| Stack | The running main stack: project `data-privacy-services`, image `c18bced81712` = HEAD, started by the gates verifier. It was not restarted or rebuilt, and no `docker compose up`/`down` was run. At the end: `/readyz` `ready: true`, `dps_llm_raw_pii_sent_total 0.0`. |
| Python / env | `.venv` of the main checkout. Contract §12.2 env is in `S1-M3-security-r2/env.sh`. |
| Probes and raw results | `~/.cache/dps-build/reports/S1-M3-security-r2/`: the scripts, `live-results-x4b5d19.json`, `write-results-x4b5d19.json`, `n1-results-x4b5d19.json`, `scan-x4b5d19.json`, `b6_offline-results.json`, `mongo_offline-results.json`, `cleanup-x4b5d19.json`, `targeted-pytest.log` and the `*.log` files. |
| Repository files modified | **none**. `git status --short` is empty, and pytest ran with `-p no:cacheprovider`. |

## 1. Method

- **Keys.** The bootstrap key minted short-lived DB keys:
  - `jobs` for tenant `default`, three keys;
  - `jobs` for the probe tenant `s1m3secr2-a`, which has **no** secret folder;
  - `jobs` + `admin` for `s1m3secr2-a`.

  Raw keys and returned key material were held in memory only. All 5 keys were disabled at the end.
- **Writes.** Every write went to one of these places:
  - connections named `s1m3secr2-x4b5d19-*`;
  - discovery jobs and snapshots;
  - one job group;
  - a scratch MongoDB database `s1m3secr2_x4b5d19`, holding 80 + 25 synthetic documents, with the profiler on
    for that database only.

  Oracle and PostgreSQL were only read, with the read-only demo logins. No Oracle or PostgreSQL target was ever
  submitted, except runs built to fail at prepare. Those carry `expected_rows: 1`, which is checked right after
  the source count and before any target is opened (`jobs_run._prepare`).
- **Canaries.** Unique strings were planted and then scanned for: a host, an inline username and password, an SQL
  literal, a MongoDB literal, a purpose ticket and a legal basis.
- **Outputs.** Every output is a status, code, boolean, id or count. Nothing value-bearing was printed or stored.

## 2. Results per lens item

**Total: 517 live checks, all passed.**
- `probe_main.py`: 463.
- `probe_write.py`: 42.
- `probe_n1.py`: 12 observations.

Plus 200 offline adversarial cases and 330 targeted tests.

### 2.1 SEC-2: cross-tenant secret refs, Oracle and MongoDB (41 checks)

| Probe (oracle and mongo each) | Result |
|---|---|
| Tenant A creates with `default/dps/src/oracle-crm-ro` or `…/mongo-crm`, with a jobs key and with an admin key | 403 `secret_ref_foreign_tenant`; the ref is not echoed |
| A foreign `options.sslrootcert_ref`, on create and on an **admin** PATCH | 403 `secret_ref_foreign_tenant` |
| `../default/…`, `/abs/x`, `dps/../../default/x`, `Default/…`, `dps/.hidden`, `dps//src/x`, `_shared/../default/x` | 422 (pattern or `secret_ref_invalid`) |
| `_shared/…` not listed for the tenant, on create and on PATCH | 403 `secret_ref_not_shared` |
| Tenant A creates the same relative ref | 201; `/test` gives `ok:false, secret_unavailable` |
| **Worker path**: discovery by tenant A on those connections | job `failed` `secret_unavailable`, 0 snapshots (Oracle and MongoDB) |
| PATCH to a foreign ref; GET afterwards | 403; the stored ref is unchanged |
| Control: the `default` tenant's own refs | 201; `/test` `ok:true` (Oracle and MongoDB) |
| A tenant-A key sending `X-DPS-Tenant: default` | 403 |

### 2.2 SEC-3: non-admin endpoint and production changes, Oracle and MongoDB (82 checks)

**Production-tagged connection, `jobs` key.** Every one of these was refused with 403 `admin_scope_required`:
- creating it with the `production` tag;
- PATCH of the host (canary), the port, a non-endpoint option, the name, an untag, restating the same tag, the
  roles, a restated `secret_ref` together with a move, an inline credential rotation, or an **empty** patch;
- DELETE.

A GET afterwards shows name, params, tags, roles, ref and `has_secret` unchanged. The canary host, username and
password are never echoed. The admin's PATCH: 200.

**Non-production connection holding credentials** (a `secret_ref`, and inline credentials sealed in the DB).
Without restated credentials, each endpoint param is refused with 403
`connection_endpoint_change_requires_credentials`, and nothing changes:
- Oracle: `host`, `port`, `service_name`, `options.protocol`, `options.tls`, `options.sslmode`.
- MongoDB: `host`, `port`, `database`, `options.replicaSet`, `options.directConnection`, `options.tls`, and
  dropping all options.

These were allowed with 200:
- non-endpoint params: Oracle `connect_timeout`, MongoDB `authSource` and `connect_timeout`;
- an endpoint change with the credentials restated (the password is not echoed);
- an endpoint change by `admin`.

A `jobs` key adding the `production` tag: 403.

**Totals:** 28 × `admin_scope_required` and 26 × `connection_endpoint_change_requires_credentials`.

**Code cross-check.** The connectors read only listed endpoint params:
- Oracle: `easy_connect` (`host`, `port`, `service_name`, `options.protocol`) or the secret's `dsn`.
- MongoDB: `from_secret` (`host`, `port`, `database`, `replicaSet`, `directConnection`, `tls`) or the secret's
  `uri`.
- `authSource` stays on the same server and is classified non-endpoint on purpose.

### 2.3 Production write gate, re-verified after fix r1 (107 + 42 checks)

**`jobs` key: 32 write cases, each refused with 403 `admin_scope_required` on `/jobs/run/validate` AND on
`/jobs/run`.**

Oracle (13 cases):
- `in_place`;
- same-table `append` with an offset key;
- `append` naming the source connection explicitly;
- `table` into the same production connection: `truncate_insert` into the existing `CRM.ADDRESS_HISTORY`,
  `upsert`, and create;
- from a non-production source into a production target (`oracle-crm-masked`): `table` `truncate_insert` into
  the existing `CRM_MASKED.CUSTOMERS_SMOKE`, `table` create, and `append`;
- `files` into a production `pvc`, and into production S3;
- synthesize `table`;
- tabular (copula) synthesize `files` into production S3.

MongoDB (14 cases):
- `in_place`;
- same-collection `append`, and `append` naming the source connection explicitly;
- `table` `truncate_insert`, `upsert` and create on the same production connection;
- from a non-production source into a production target: `table` `truncate_insert`, `upsert` and create, and
  `append`;
- `files` into a production `pvc`, and into production S3;
- synthesize `table`;
- a **job-group** run into a production target.

PostgreSQL (5 cases):
- `table` `truncate_insert` into the existing `bank_uat.customers_smoke`, and `table` create;
- `append`;
- `files` into a production `pvc`, and into production S3.

**Other results:**
- **No job was created** by any refused run (DB count of runs carrying the probe ticket: 0 before, 0 after).
- **The admin passes the gate** in all 32 cases. 29 answer `{"ok": true}`. Oracle `in_place` continues to
  `pk_transform_in_place` and the two same-connection Oracle `append`s to `length_overflow`, both later checks.
- **Controls** with the `jobs` key:
  - A production source into a non-production target is allowed (200), for Oracle `table` and `files` and
    MongoDB `table` and `files`. This is the documented `jobs` right.
  - `in_place` naming another connection: 422 `in_place_invalid`.
  - `table` or `files` without `connection_id`: 422 `target_invalid`. The gate cannot be skipped by leaving the
    target out.

**Retry (`probe_write.py`, 34 checks).** The fixture is an admin's production run that fails at prepare with
`row_count_mismatch` before any write. Four were used:
- MongoDB `in_place`;
- Oracle `table` into the production `oracle-crm-masked` target;
- Oracle `files` into production S3;
- MongoDB `files` into a production `pvc`.

In each case:
- A `jobs` key's retry gets 403 `admin_scope_required`, both **bare and with the job's own key material**.
- The job stays `failed`.
- The admin's bare retry passes the gate and gets 409 `key_material_required`.
- The admin's retry with the key gets 200 `queued`, and the job fails the same check again.

Control: the `jobs` key's own non-production failed run passes the gate (409, then 200). Evidence that nothing
was written:
- The Oracle retry table was never created (catalog count 0).
- The scratch source documents are unchanged (80 before and after).

**Idempotency replay.** An admin submits a production run with an `Idempotency-Key`. The `jobs` key sends the same
body with the same key and gets **403**. The admin's job id and key are never echoed. The admin's own replay gets
200 with the same job and no key material.

**B1 live repro (round 1's exact case).**
- The `jobs` key's `table` `truncate_insert` into the existing production collection gets 403 on validate and on
  run, and the 25 victim documents are untouched.
- The **admin's identical run then succeeded**, leaving 0 victim documents and 80 written. So the gate is what
  stops the `jobs` key, and the gate does not over-block.

### 2.4 M3-B6: selection column re-mapping (73 live checks + 123 offline candidates)

**Live, `POST /connections/{id}/count`, 30 cases, each refused with 422 before any query runs:**
- 25 × `selection_query_alias`, 4 × `selection_parse_error`, 1 × `selection_multiple_statements`.
- Round 1's PostgreSQL cases: column-list aliases with a named list and with `*`, a CTE column list,
  `email AS segment`, a subquery named like the object, a CTE shadow, a parenthesised `NATURAL FULL JOIN`,
  `UNPIVOT`.
- Round 1's Oracle cases: `UNPIVOT` named and `*`, `PIVOT` named and `*`, `MATCH_RECOGNIZE`, an alias list, a
  CTE column list, `EMAIL SEGMENT`, a CTE shadow, `MODEL`.
- **New in round 2:**
  - PostgreSQL: `email AS "segment"` (quoted, same name), `json_populate_record(null::bank.customers, …)`, a
    `U&"segm\0065nt"` escaped alias, a natural inner join.
  - Oracle: `EMAIL AS "SEGMENT"`, multi-column `UNPIVOT ((SEGMENT, COUNTRY) FOR …)`, lower-case `unpivot`,
    `UNPIVOT` inside a `WHERE` subquery, `PIVOT XML`, lower-case `pivot`, `WITH FUNCTION` (PL/SQL), a natural
    inner join.

**Live, other routes:**
- `POST /jobs/run/validate` (`files` target, accepted snapshots; control `{"ok": true}`): all 30 are refused.
- `POST /jobs/run`: 4 cases, each 422, and no job is queued.
- **Discovery** with a remap query (PostgreSQL alias list, Oracle `UNPIVOT`, Oracle `PIVOT *`): the worker refuses
  it against the described shape. Job `failed` with `selection_query_alias`, no snapshot.

**Controls.**
- Legitimate queries are counted.
- Different-case aliases are accepted and **never read**: `email AS "SEGMENT"` (PostgreSQL), and the Oracle quoted
  lower case.
  - PostgreSQL: the outer select of the live `runs_as` does not read `segment`.
  - Oracle: the shipped `read_projection` gives `CUSTOMER_ID` only (offline); every connector reads exactly that
    projection.

**Offline** (`b6_offline_r2.py`: the shipped validator bound exactly as the API binds it): 123 candidates (71 from
round 1 + 52 new).
- 107 are refused.
- 16 are accepted: 8 controls, plus 8 benign cases (different-case aliases, which are never read; a self-join on the
  key; `ORDER BY`; `DISTINCT ON`; `TABLESAMPLE`; `ONLY`; `SAMPLE`). None puts one column's values under another
  column's name.
- 0 crashes.
- New refusals include: `ROWS FROM … AS t(…)`, a recursive CTE column list, `crosstab` (denied), `TABLE` inside a
  subquery, `XMLTABLE`, aggregate aliases (`max`, `string_agg`, `LISTAGG`), `CONNECT_BY_ROOT`, `LATERAL`,
  `OUTER APPLY`, `TABLE(SYS.ODCIVARCHAR2LIST(…))` (denied), `DBMS_LOCK`/`UTL_HTTP` (denied), `ROWID AS SEGMENT`,
  two unqualified sources, `FOR UPDATE`, `INTO`, multiple statements, `$1` binds, `set_config`.

### 2.5 MongoDB filter allow-list (103 live checks + 77 offline cases)

**`POST /connections/{id}/count`, 77 cases:**
- 50 × `selection_operator_denied`, 14 × `selection_invalid`, 2 × `selection_identifier_invalid`, 2 ×
  `selection_too_complex`, 1 × `selection_regex_too_long`.
- 8 controls are counted (200). The slowest refusal took 9 ms.
- Round 1's 46 cases: `$where` at top level, in `$and`/`$or`/`$nor`, as a field operator, under `$not`, inside
  `$elemMatch`, inside `$in`/`$all` items and literals, as `$WHERE`, and with a trailing space; `$function` and
  `$accumulator` in `$expr`, as field operators and nested; `$expr`, `$jsonSchema`, `$text`, `$comment`, `$mod`,
  `$options`, `$geoWithin`, `$sampleRate`, `$bitsAllSet`; Extended JSON `$code`, `$regularExpression`,
  `$binary`, `$minKey`; limits.
- **New in round 2, 31 cases:**
  - `$where`/`$function`/`$accumulator` nested 4–7 levels through `$and`/`$or`/`$nor`/`$not`/`$elemMatch`/
    `$all`/`$in` literal documents;
  - as malformed arguments of `$type`, `$size`, `$exists`, `$regex`, `$in`, `$and`, `$not`;
  - `$where` with a NUL suffix or a zero-width character;
  - `$where: {$code}`;
  - `$date`/`$oid`/`$numberLong` wrapping an operator;
  - stage-like `$lookup`/`$merge`/`$out`;
  - `$expr` in `$elemMatch` and under `$not`.

  All are refused except the 3 round-2 controls and one literal embedded document with a dotted key, which is a
  plain equality match and runs no operator.

**Other routes:**
- `/jobs/discover`: 6 forbidden filters, each 422 `selection_operator_denied`, no job.
- `/jobs/run/validate`: 12 forbidden filters, each 422 (control `ok`).
- `/jobs/run`: 2 forbidden filters, each 422, no job.
- Engine mismatch both ways: 422 `selection_engine_mismatch`.
- `columns` naming `$where`, `accounts.$` or `$function`: 422 `selection_identifier_invalid`.

**Server-side proof (scratch database profiler).**
- 195 entries, 188 from the DPS connector.
- **0** contain `$where`, `$function`, `$accumulator`, `$expr`, `$jsonSchema`, `$text` or `$code`.
- The accepted discovery filter's literal appears once: the positive control that filters reach the profiler.
- Every command that carries a user filter (`find`, and `aggregate` for `count_documents`) carries `maxTimeMS`.
  The 22 commands without it are filter-free `estimated_document_count` metadata counts.

### 2.6 Value-free records (`scan-x4b5d19.json`)

**Needles.** 31 needles in all: the 7 canaries; query texts (`UNPIVOT`, `unpivot`, `MATCH_RECOGNIZE`,
`PIVOT XML`, `WITH FUNCTION`, `json_populate_record`, `U&"segm`, `email AS segment`, `EMAIL SEGMENT`, `CAST(email`,
`TO_NUMBER(EMAIL`); the operators `$where`/`$function`/`$accumulator`/`$expr`; 6 secret-ref strings; and the
scratch values `@example.invalid`, `Probe Person`, `Victim Doc`.

**Audit rows** (DB as superuser, every tenant, from the probe start): 487 rows. **0** needle hits and **0** rows
holding an e-mail address.
- **Routes** are templates.
- **`counts` keys** are names only (`field.params.host`, `field.secret_ref`, `rows_written`, …).
- **Refused attempts are audited:**
  - `connection.update` rejected 403 ×56;
  - `job.create` rejected 403 ×34 (every refused run);
  - `job.retry` rejected 403 ×8 (every refused retry);
  - `connection.count` rejected 422 ×108;
  - `connection.create` rejected 403 ×10.

The admin audit API CSV for the probe tenants (200, 487 lines): 0 needles, 0 e-mail addresses.

**Job rows** (17). Selections were sealed in 13. 0 SQL or MongoDB literal canaries and 0 e-mail addresses appear in
clear, and 0 runs carry the refused-run ticket. One discovery spec holds its query *template* (an `UNPIVOT` text with
no literal): templates in `spec` and values sealed is the contract's design.

**Snapshots** (5). 0 canaries and 0 literal values. The two MongoDB snapshots hold e-mail *shapes*
(`aaaaa99@aaaaaaa.aaaaaaa`, letters mapped to `a`), which is the shape abstraction, not a value.

**Connections.** The 2 inline-credential connections are sealed. The canary username and password appear 0 times.

**Container logs** (app and worker, from the probe start, 577 lines):
- 0 needle hits, 0 lines with an e-mail address, 0 tracebacks, 0 error-level lines.
- 2 value-free `*_call_failed` warnings, which are the provoked driver errors.

**Driver errors carrying a value.** These were provoked through `/count`: a PostgreSQL `CAST(email AS integer)`, an
Oracle `TO_NUMBER(EMAIL)` and an Oracle `TO_DATE(FULL_NAME)`. Each answers 502 `source_error` with a 44–65 byte body
and no `@`.

### 2.7 Tenancy (23 checks) and targeted tests

**Tenancy.** Another tenant's key gets 404 on GET, PATCH, DELETE, `/test`, `/count`, `/objects` and
`POST /jobs/discover`. This holds for the production Oracle connection, the production MongoDB connection and tenant
A's connection. Another tenant's snapshot gives 404. A run validate naming another tenant's ids gives 404.

**Targeted tests** (isolated test database): **330 passed**. Round 1 had 276; the new tests are
`test_production_write_gate.py` and the new `test_targets.py` cases. The files run were `test_production_write_gate.py`,
`test_targets.py`, `test_api_connections_m3_security.py`, `test_connection_endpoints.py`, `test_secrets.py`,
`test_selection{,_mongo,_oracle}.py` and `test_api_connections.py`.

## 3. Blocking findings

None.

## 4. Non-blocking notes

- **N1: the untagged-clone bypass of the production gate is still open on main.** This is S3 auth-tenancy F1
  (fsec F-01), fixed on `fsec` by `f112044` and not merged. Re-confirmed by validation only; nothing was
  submitted. A `jobs` key reads a production target's `params`/`secret_ref` through GET and creates an untagged
  clone (201). A `table` or `files` run into the clone then validates (200), while the same run into the tagged
  original is 403. This held for Oracle, MongoDB, S3 and `pvc` targets.
- **N2: the fsec fix will not cover a `pvc` clone.** Fsec binds "production" to a connection's `secret_ref`
  (`holds_production_credentials`).
  - A `pvc` connection holds no credentials, so after that merge a `jobs` key can still create an untagged `pvc`
    with a production `pvc`'s `base_path` and write `files` there.
  - Files land under a fresh `<job_id>/` directory, so this adds data to production storage rather than
    overwriting it. A loader that ingests every manifest under the prefix would still take it.
  - The fsec review does not discuss it: §4 refutes only the cross-tenant `pvc` case.
  - Suggested fix: treat the resolved `base_path` of a production-tagged `pvc` (equal, nested or enclosing) as a
    production marker for non-admin creates, patches and writes. Add a test at the fsec merge.
- **N3: job controls of an admin's production run need only `jobs`.** `_control` (cancel, pause, resume) checks
  only the `jobs` scope.
  - Resume was already noted by fix r1 (it continues an authorised run).
  - Cancel is the sharper case: a cancelled run is **not** retryable (`retry` accepts `failed`/`dead` only). A
    `jobs` key can therefore stop an admin's production `in_place` run part-way and leave the table partly
    rewritten, with no way to resume it.
  - This is integrity, not privacy, and outside the contract's gated set (§8.1). It was established from code, not
    run live. Suggested for the fsec redesign: `admin` for cancel and pause of a run that writes a production
    connection.
- **N4 (low, defence in depth): Oracle reads are not in a read-only transaction.** PostgreSQL reads use
  `default_transaction_read_only=on` + `BEGIN READ ONLY`. Oracle relies on:
  - the deny-list (`dbms_`, `utl_`, `sys.`, `xmltype`, `httpuritype`);
  - Oracle itself refusing `NEXTVAL` in subqueries and `WHERE`;
  - `WITH FUNCTION` being refused (`selection_multiple_statements`).

  A user-defined autonomous-transaction function would still run. This is a DB-hardening matter (read-only logins)
  for the injection lens.
- **N5 (functional, fail-closed): `$and`/`$or` inside an `$elemMatch` document is refused as `selection_invalid`,**
  even with harmless content. No security impact.
- Carried from round 1 and not re-tested (code unchanged):
  - **Unqualified table names.** The PostgreSQL connector does not pin `search_path`, and Oracle does not pin
    `CURRENT_SCHEMA`, so the table an unqualified name reads depends on the login.
  - **Unknown-option 422.** A 422 for an unknown option names the caller's own option key.

## 5. Not covered, and why

- **A positive `_shared/` reference and a symlink escape.** Both need writes to the shared secret volume. The unit
  tests in `test_secrets.py` cover them (passed).
- **A real Oracle or PostgreSQL write by an admin.** It was not run, to protect the shared demo data. The admin's
  end-to-end path was proven on the MongoDB scratch database (§2.3).
- **N3 live.** It needs a long-running production `in_place` job, so it rests on code reading.

## 6. Residue on the main stack (value-free)

All of the following is value-free or synthetic:
- 17 jobs (discover and failed runs, plus the admin's scratch `table` run and its evidence pack);
- 5 snapshots;
- 1 job group `s1m3secr2-x4b5d19-grp`;
- 5 disabled probe keys;
- the `dps_tenant` row `s1m3secr2-a`;
- the probe's audit rows.

There is no delete route for jobs, snapshots or groups; retention removes them. Removed or confirmed absent:
- 27 probe connections (all deleted, 0 kept);
- the scratch MongoDB database (dropped);
- Oracle and PostgreSQL probe tables (0);
- export-root directories (0);
- MinIO objects under `s1m3secr2/` (0).

## 7. Reproduce

All commands run from the main checkout after `source ~/.cache/dps-build/reports/S1-M3-security-r2/env.sh`:

```bash
P=~/.cache/dps-build/reports/S1-M3-security-r2
.venv/bin/python $P/b6_offline_r2.py $P/b6_offline-results.json
.venv/bin/python $P/mongo_offline_r2.py $P $P/mongo_offline-results.json
.venv/bin/python $P/probe_main.py                       # writes state-<run>.json, live-results-<run>.json
.venv/bin/python $P/probe_write.py $P/state-<run>.json  # retry, idempotency, B1 live
.venv/bin/python $P/probe_n1.py    $P/state-<run>.json  # N1/N2 clone observations (validation only)
.venv/bin/python $P/scan_r2.py     $P/state-<run>.json  # value-free scan
.venv/bin/python $P/cleanup_r2.py  $P/state-<run>.json
```
