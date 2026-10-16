# S1-M3: post-merge verification of main after the M3 merge (security lens, round 1)

**Verdict: FAIL.** There is 1 blocking finding, **B1**. The production gate does not cover `table` or `files`
writes. A `jobs`-only key emptied an existing collection on a production-tagged MongoDB connection.

Everything else in the lens holds on Oracle and MongoDB through the live API:
- SEC-2 (tenant-scoped secret refs, on the API path and on the worker path).
- SEC-3 (an endpoint or production change needs credentials or `admin`).
- The `in_place`/`append` admin gate.
- M3-B6 (column re-mapping is refused).
- The MongoDB filter allow-list.
- Audit rows, logs and job rows hold no values.

Two other ways around the production gate, both already filed by the S3 finders, are re-confirmed on the M3
engines (N1, N2). They are non-blocking here because each is owned by an S3 finding.

| | |
|---|---|
| Date | 2026-10-14, 17:45–18:20 IST (probe window 12:30:55Z–12:50Z) |
| Tree verified | `main` @ `144cc65` (clean). It contains the M3 merge `b878c50` (ancestor checked) and the integrator's `fix/test(integrate)` commits. |
| Stack | The running main stack (project `data-privacy-services`, ports 8400/5441/5442/1522/27018). It was started by the gates verifier. It was **not** restarted or rebuilt, and no `docker compose up`/`down` was run. `/readyz` was `ready: true` at the end and `dps_llm_raw_pii_sent_total 0.0`. |
| Python | `.venv` of the main checkout (httpx, pymongo, oracledb). Contract §12.2 env from `~/.cache/dps-build/s1-m3-gates-r1/env.sh`. |
| Probes and raw results | `~/.cache/dps-build/reports/S1-M3-security-r1/` (scripts, `*-results-*.json`, `scan-r344ab9.json`, logs) |
| Repository files modified | **none** (`git status --short` is empty). Targeted pytest ran with `-p no:cacheprovider`. |

## 1. Method

- **Keys.** The probe minted short-lived DB keys through `POST /admin/keys` with the compose bootstrap key:
  - `jobs` scope for tenant `default`;
  - `jobs` scope for tenant `s1m3sec-a`;
  - `jobs` + `admin` scope for tenant `s1m3sec-a`.

  The raw keys were held in memory only. All keys were disabled at the end.
- **Tenants.** The demo tenant `default` owns the only secret folder, holding the demo refs. Tenant `s1m3sec-a`
  has **no** folder. This makes "same relative ref, other tenant" a real test.
- **Data written.** All writes went to these places only:
  - new connections, snapshots and discovery jobs with `s1m3sec-<run>` names;
  - a scratch MongoDB database `s1m3sec_r344ab9` holding 80 synthetic documents, with the profiler on (level 2,
    that database only);
  - run submissions that were expected to be refused.

  Oracle and PostgreSQL were read only, with the read-only demo logins (`dps_ro` has SELECT only; `dps_reader`).
  The only run that wrote data ran entirely inside the scratch database.
- **Canaries.** Unique strings were sent and then scanned for: a host, an inline username and password, an SQL
  literal, a MongoDB literal, and a purpose ticket and legal basis. Every output is a status, code, boolean or
  count. No key, secret or customer value was printed or stored.
- **Cleanup.** 18 + 1 connections deleted, 10 keys disabled, scratch database dropped. Section 6 lists what
  remains on the stack.

## 2. Results per lens item

Total: 277 live checks (`live-results-r344ab9.json`) plus the follow-ups below. **0 unexpected outcomes.** The
`PRODGATE_CLONE` rows record observations, not passes (see N1).

### 2.1 SEC-2: cross-tenant secret refs, Oracle and MongoDB (35 checks)

| Probe (oracle and mongo each) | Result |
|---|---|
| Tenant A creates a connection whose ref names `default/dps/src/oracle-crm-ro` / `…/mongo-crm`, with a jobs key and with an admin key | 403 `secret_ref_foreign_tenant`; the body never echoes the ref |
| Foreign `options.sslrootcert_ref` on create; on PATCH by tenant A's **admin** | 403 `secret_ref_foreign_tenant` |
| `../default/…`, `/abs/x`, `dps/../../default/x`, `Default/…`, `dps/.hidden` | 422 (pattern or `secret_ref_invalid`) |
| `_shared/…` not listed for the tenant, on create and on PATCH | 403 `secret_ref_not_shared` |
| Tenant A creates the **same relative ref** `dps/src/oracle-crm-ro` / `dps/src/mongo-crm` | 201, then `POST /test` gives `ok:false, secret_unavailable`: the ref resolves under A's own folder, never `default`'s |
| The same, on the **worker path**: discovery by tenant A on those connections | job `failed`, `secret_unavailable`, 0 snapshots (`followup-results`) |
| PATCH of A's connection to a foreign ref; GET afterwards | 403; the stored ref is unchanged |
| Control: the demo tenant's own ref (jobs key, tenant `default`) | 201 and `/test` `ok:true` (Oracle and MongoDB) |
| Tenant-A key sending `X-DPS-Tenant: default` | 403 |

### 2.2 SEC-3: non-admin endpoint and production changes, Oracle and MongoDB (74 checks)

**Production-tagged connections, `jobs` key.** Every one of these was refused with 403 `admin_scope_required`:
- creating a connection with `tags: [production]`;
- PATCH of the host (a canary host), the port, a non-endpoint option, the name, the tag (untag), or the roles;
- restating `secret_ref` together with a move;
- rotating inline credentials;
- DELETE.

A GET afterwards shows name, params, tags, roles, ref and `has_secret` unchanged. Responses never echo the
canary host, username or password. The admin key's PATCH: 200.

**Non-production connections holding credentials.** Both holders were tested (a `secret_ref`, and inline
credentials sealed in the DB). Without restated credentials, every endpoint param was refused with 403
`connection_endpoint_change_requires_credentials`, and nothing changed:
- Oracle: `host`, `port`, `service_name`, `options.protocol`, `options.tls`.
- MongoDB: `host`, `port`, `database`, `options.replicaSet`, `options.directConnection`, `options.tls`.

These were allowed with 200:
- non-endpoint params: Oracle `connect_timeout`, MongoDB `authSource` and `connect_timeout`;
- an endpoint change with the credentials restated (the canary password is not echoed);
- an endpoint change by `admin`.

A `jobs` key adding the `production` tag: 403.

**Coverage cross-check (code).** The connectors take their endpoint from these fields only:
- Oracle `easy_connect`: `host`, `port`, `service_name`, `options.protocol`, or the secret's `dsn`.
- MongoDB `from_secret`: `host`, `port`, `database`, `replicaSet`, `directConnection`, `tls`, or the secret's
  `uri`; `authSource` stays on the same server.

All of these are in `ENDPOINT_PARAMS`, so no endpoint-deciding param is missing.

### 2.3 Production gate (20 checks + follow-ups)

| Probe (`jobs` key unless stated) | Result |
|---|---|
| `in_place` on a production Oracle connection (`CRM.CUSTOMERS`, accepted snapshot): `/jobs/run/validate` and `/jobs/run` | 403 `admin_scope_required`; 0 jobs carry the probe's ticket |
| Same-table `append` (offset) on production Oracle | 403 on both routes |
| `in_place` on production MongoDB; `append` into a production MongoDB target | 403 on both routes |
| Admin key, same specs | passes the gate (later codes `pk_transform_in_place`, `length_overflow`, `ok`, `pk_strategy_invalid`); without `confirm_write`: 422 `confirm_write_required` |
| **`table` + `truncate_insert` into an EXISTING collection of a production-tagged MongoDB connection (target role)** | **validate 200; submitted 202, job succeeded, plan `truncated: true`; 25 pre-existing documents became 0, replaced by 80. No admin, no `confirm_write`: B1** |
| `append` into that same target with the same key | 403 `admin_scope_required` (the inconsistency) |
| `table` + `truncate_insert` into an existing Oracle table (`CRM.ADDRESS_HISTORY`) on a production-tagged connection | validate 200 (never submitted) |
| `table` upsert into an existing production collection; `table` creating a new collection in production; `files` into a production-tagged pvc | validate 200 for each |
| Untagged clone of the production Oracle / MongoDB connection (same params and `secret_ref`, both read via GET) | 201; discover and accept OK; `in_place` validate on the clone: MongoDB `ok`, Oracle continues to `pk_transform_in_place`. The gate is bypassed (N1, S3 auth-tenancy F1). Validation only. |
| Retry of an admin's failed production `in_place` (it failed at prepare with `row_count_mismatch` before any write) | without a key: 409 `key_material_required` (**no admin re-check**); with the job's public kid and the retrier's own DEK: **200 requeued**. It failed the same prepare check again; scratch source unchanged at 80/80 (N2, S3 crypto-custody F3). |

### 2.4 M3-B6: selection column re-mapping (49 live checks + 71 offline candidates)

**Live, through the API**, with the PG `bank.customers`, Oracle `CRM.CUSTOMERS` and MongoDB scratch objects:

- **`POST /connections/{id}/count`** refuses each of these with 422 before any query runs:
  - PG column-list alias `AS c(customer_id, …, segment)`, with a named list and with `*`;
  - CTE column list `WITH x(customer_id, segment)`;
  - `email AS segment`;
  - a subquery aliased like the object;
  - a CTE that shadows the object;
  - a parenthesised `NATURAL FULL JOIN`;
  - Oracle `UNPIVOT` (named and `*`), `PIVOT` (named and `*`), `MATCH_RECOGNIZE … MEASURES EMAIL AS SEGMENT`;
  - an Oracle alias list, a CTE column list, `EMAIL SEGMENT`, a CTE shadow.

  The code is `selection_query_alias` for all of these, except PG `UNPIVOT` and Oracle `MODEL`, which are
  `selection_parse_error` (fails closed).
- **`POST /jobs/run/validate`** (`files` target, accepted snapshots; the control spec is `{"ok": true}`): all 18
  cases are refused with 422 `selection_query_alias` (or `selection_parse_error` for `MODEL`).
- **`POST /jobs/run`**: the PG alias list and the Oracle UNPIVOT give 422 `selection_query_alias`. No job is
  queued.
- **Discovery** with a remapping query (PG alias list, Oracle UNPIVOT) is accepted at submission, because no
  shape exists yet. The worker then refuses it against the described shape: job `failed`,
  `selection_query_alias`, no snapshot.
- **Controls**:
  - Legitimate queries are counted.
  - `email AS "SEGMENT"` (PG) and `EMAIL AS "segment"` (Oracle) are different-case names, so not columns of the
    object. They are accepted and **never read**. The PG `runs_as` outer select is `SELECT q."customer_id"` only.
    Oracle reads `t."COL"` by exact quoted name (`oracle_sql.read_sql`).
- **Offline adversarial set** (`b6_offline.py`, the shipped validator, bound exactly as the API binds): 71
  candidates, 60 refused. The 11 accepted are 6 controls plus 5 benign cases: two different-case aliases (not
  read), a self-join of the same column, `ORDER BY` another column, and `DISTINCT ON`. No accepted query puts one
  column's values under another column's name. Among the refused candidates:
  - lateral joins, `CROSS`/`OUTER APPLY`, comma joins with `*`, `USING` and `NATURAL FULL` merges (bare and
    parenthesised);
  - `VALUES`, `unnest`, `jsonb_to_record` column definitions;
  - row-type attributes, scalar subqueries, casts, parentheses, `COALESCE`/`DECODE`, window functions;
  - `UNION` (top level and inside `FROM`);
  - `JSON_TABLE`, `XMLTABLE` (denied function);
  - a db link (invalid identifier) and flashback (parse error).
- **Tests on main**:
  - `tests/connectors/test_run_modes.py::test_a_column_list_alias_or_pivot_cannot_remap_a_column` (PG e2e);
  - `tests/connectors/test_oracle_pipeline.py::test_unpivot_pivot_or_match_recognize_cannot_remap_a_column`
    (Oracle e2e).

  Both are part of the gates verifier's full run (2617 passed). Fix commit: `de757f5`.

### 2.5 MongoDB filter allow-list (63 live checks + 46 offline cases)

**`POST /connections/{id}/count`**, 42 refusals, all answered in about 1 ms with the expected code:
- `$where` at the top level, in `$and`/`$or`/`$nor`, as a field operator, under `$not`, inside `$elemMatch` (as a
  document and as a condition), as an `$in`/`$all` item, inside an `$eq` literal and inside an embedded literal;
- `$WHERE`, and `"$where "` with a trailing space;
- `$expr: {$function}`, `$function` as a field operator, `$function` nested under `$and`;
- `$expr: {$accumulator}`, `$accumulator` as a field operator and inside `$elemMatch`;
- a plain `$expr`, `$jsonSchema`, `$text`, `$comment`, `$mod`, `$options`, `$geoWithin`, `$sampleRate`,
  `$bitsAllSet`;
- Extended JSON `$code` (2 forms), `$regularExpression`, `$binary`, `$minKey`.

These four controls are counted (200): `ctl_eq`, `ctl_in_regex`, `ctl_elemMatch`, and `ctl_oid_date` (Extended
JSON `$oid`/`$date`).

**Other refusal codes** on the same route:
- `selection_invalid`: a bad `$oid`, a top-level `$eq`, operators mixed with plain keys;
- `selection_identifier_invalid`: `profile.$where`, a full-width `＄where`;
- `selection_regex_too_long`: a regex of 257 characters;
- `selection_too_complex`: 9 levels of nesting, an `$in` of 1001 items.

**Other routes:**
- `POST /jobs/discover` with `$where`, `$function`, `$accumulator` or an `$elemMatch` `$where`: 422
  `selection_operator_denied`, no job.
- `/jobs/run/validate` with 8 forbidden filters (control `ok`): 422.
- `POST /jobs/run` with `$where`: 422, no job.
- Engine mismatch both ways (MongoDB with SQL `where`; Oracle with `mongo_filter`): 422
  `selection_engine_mismatch`.

**Server-side proof** (scratch database profiler): 166 entries, 152 from the DPS connector (20 of them counts).
0 contain `$where`, `$function`, `$accumulator`, `$expr`, `$jsonSchema`, `$text` or `$code`. The literal of an
accepted filter shows up once, which is the positive control that filters reach the profiler. Every MongoDB read
and count carries `maxTimeMS`, so a slow `$regex` (at most 256 characters) is bounded server-side.

**Error bodies.** Refused selections answer `{detail, code, checks}` only. No query text and no literal is echoed
(PG, Oracle and MongoDB checked with canary literals, `echo-results`).

### 2.6 Value-free records (`scan-r344ab9.json`)

**Audit rows.** All tenants, from the probe start, scanned in the DB (superuser, RLS bypassed for the scan): 286
rows. 0 hits for any of these needles:
- the 7 canaries: host, password, username, SQL literal, MongoDB literal, ticket, basis;
- the query texts `UNPIVOT`, `MATCH_RECOGNIZE` and `email AS segment`;
- the operators `$where`, `$function`, `$accumulator`, `$expr`;
- the ref strings `default/dps/src`, `dps/src/oracle-crm-ro`, `_shared/`.

The admin audit API CSV for the two probe tenants (200, 286 lines) also has 0 hits.

What the audit rows hold:
- **Routes** are templates only (`/api/v1/connections/{connection_id}`, …).
- **`counts` keys** are names only (`field.params.host`, `field.credentials`, `field.secret_ref`, `objects`,
  `rows_written`, …).
- **`mode`/`policy`** hold names or an inline-policy hash.
- **Refused attempts are audited** (rejected): 48 `connection.update` 403, 67 `connection.count` 422,
  10 `connection.create` 403, 10 `job.create` 422, and so on.

**Job rows.** 12 jobs. Selections were sealed in 8 (`spec_enc`). 0 canaries appear in clear. The stored specs
hold templates (`:l1` in 2, MongoDB `?` in 1). 0 runs carry the probe's ticket, so no refused run created a job.

**Snapshots.** 5 rows, 0 canaries.

**Connections.** The 2 inline-credential connections are sealed. The canary username and password appear 0 times
in clear.

**Container logs** (app and worker, since the start): 327 lines, 0 hits for every needle, 0 tracebacks.

**Validation 422s.** These answer `{type, loc, msg}` only. A model-level 422 that carried a canary password did
not echo it.

### 2.7 Tenancy (18 checks)

Another tenant's key gets 404 on every one of these, for the production Oracle connection and for tenant A's
connection:
- GET, PATCH, DELETE;
- `/test`, `/count`, `/objects`, `/describe`;
- `POST /jobs/discover` naming the connection.

Another tenant's snapshot gives 404. A run validate naming another tenant's ids gives 404 `connection_not_found`.

### 2.8 Targeted tests (isolated `dps_test_<pid>` database, temp secret folders)

`pytest -q` was run on these files: `tests/connectors/test_selection.py`, `test_selection_mongo.py`,
`test_selection_oracle.py`, `tests/test_connection_endpoints.py`, `tests/test_secrets.py`,
`tests/api/test_api_connections_m3_security.py` and `tests/api/test_api_connections.py`.

Result: **276 passed** (`targeted-pytest.log`). Tests that reseed the shared demo `CRM.*` tables were
deliberately not run (HANDOFF trap); the gates verifier ran the whole suite.

## 3. Blocking finding

### B1 (HIGH): the production gate covers only `in_place`/`append`; a `jobs` key can truncate an existing production table or collection through the `table` mode

**Where.** `dps/layers/connectors/targets.py`, `validate_run` (lines 654–662). The admin check lives inside
`if mode in _DATABASE_WRITES` (`{"in_place", "append"}`). The written connection of a `table` or `files` run is
never checked for the `production` tag.

**Contract and intent.** The contract (§5.3, §8.1) and the M2 plan scope the gate to `in_place`/`append`.
However, the gate exists to stop non-admin mutation of production data (spec D3). The console's own
"Tagged production" switch tells users "writes need the admin scope" (M2 plan, step 1 of the wizard).

**Engines affected.** The gap is engine-independent (since M2) and is now reachable on M3's writers:
- MongoDB `_prepare_table` runs `delete_many({})` on an existing collection;
- Oracle `_truncate` runs `TRUNCATE TABLE`, or `DELETE` when the login lacks the privilege.

**Evidence** (`probe_tablemode.py`, `tablemode-results-r344ab9.json`):
- A `jobs`-only key (tenant `default`, no `admin`) submitted `table` + `truncate_insert` from the scratch
  `people` collection into the existing `people_victim` collection. The target was a production-tagged MongoDB
  connection with the `target` role.
- The run needed no `confirm_write`. Job `j_01M4X6KEZCVKWEN39W2EWNS96K` succeeded with plan `truncated: true`.
  The 25 pre-existing documents became 0, replaced by 80.
- The same key's `append` into the same target was refused with 403 `admin_scope_required`.
- Validation (the only place the gate lives) also accepted the following for the same key, none of them
  submitted:
  - Oracle `table` truncate into the existing `CRM.ADDRESS_HISTORY` on a production-tagged connection;
  - `table` upsert into an existing production collection;
  - `table` create in production;
  - `files` into a production-tagged pvc.

  Prepare then requires the existing target to be structurally compatible. This was shown on MongoDB with a
  compatible collection; an incompatible one fails `target_invalid`.

**Impact.** Take a production connection with the target role, which is the configuration an admin needs for
production `in_place` work. Any `jobs` key of the tenant can empty or overwrite any structurally compatible table
or collection on it. For example, a masked UAT copy loaded over the production table of the same name, from a
non-production source connection. This is more destructive than the `append` that *is* gated. Combined with N1
(untagged clone), the target-role precondition disappears.

**Fix.** In `validate_run`, require `admin` whenever the **written** connection is production-tagged, for every
write mode: `table` and `files` targets as well as `in_place`/`append`. Keep `confirm_write` for
`in_place`/`append`.

Apply the same check on `POST /jobs/{id}/retry`. This closes N2's gate half; the kcv binding is S3
crypto-custody F3.

Tests on PG, Oracle and MongoDB, each with a `jobs` key giving 403 `admin_scope_required` and an admin control:
- `table` `truncate_insert`, `table` `upsert` and `files` into a production-tagged target;
- retry of a production `in_place`.

Amend contract §5.3/§8.1 and the HANDOFF "Who may change a connection". Fold this into the S3 auth-tenancy F1
redesign (production bound to the resource), which touches the same gate.

## 4. Non-blocking findings and notes

- **N1: untagged-clone bypass of the production gate, re-confirmed on Oracle and MongoDB.** This is S3
  auth-tenancy Finding 1 (HIGH, owned by the `fsec` stream), with the documented HANDOFF residual "a `jobs` key
  may still create a connection that names any of its own tenant's `secret_ref`s".
  - A `jobs` key reads the production connection's `params` and `secret_ref` (GET).
  - It creates an untagged clone with roles `[source, target]` (201), then discovers and accepts it.
  - `in_place` validates on the clone. Validation only; nothing was submitted.

  Not repeated as blocking, to avoid two parallel redesigns of one gate. The S3 fix must cover Oracle and MongoDB
  connections, and B1's write modes.
- **N2: retry has no admin re-check, re-confirmed on MongoDB.** This is S3 crypto-custody F3 ("aggravation").
  - A `jobs` key retrying an admin's failed production `in_place` gets 409 `key_material_required`, not 403.
  - With the job's public kid (visible via `GET /jobs/{id}`) and its own DEK, the retry is accepted (200,
    `queued`).
  - In the probe it failed the same `row_count_mismatch` prepare check, so nothing was written.
- **N3 (low): unqualified table names in a query.** The validator treats an unqualified table name as the object
  (contract rule), but the PG connector does not pin `search_path` (Oracle: `CURRENT_SCHEMA`). Which table it
  reads depends on the login's search path. Here, `FROM customers` for `bank.customers` answered
  `object_not_found`. Suggested fix: pin `search_path`/`CURRENT_SCHEMA` to the object's owner for query reads, or
  treat an unqualified name as the object only when the owner is the login's default schema.
- **N4 (cosmetic):** a 422 for an unknown connection option names the caller's own option key in `msg` (no
  value, no password).
- **N5: reading the SEC-3 design.** Restating a `secret_ref` (a name a `jobs` key can read via GET) counts as
  resupplying credentials. The endpoint-change rule therefore protects inline (sealed) credentials fully, while
  `secret_ref` connections rely on the production tag and admin. This is consistent with the contract and covered
  by N1.

## 5. What was not covered and why

- **A positive `_shared/` reference.** It would need a write to the shared secret volume. Unit tests cover it
  (`tests/test_secrets.py`, passed).
- **A symlink escape at load time.** This is also covered by `tests/test_secrets.py`.
- **The prepare-time re-validation of B6** (`jobs_run.py:494`, `oracle.py:607`). It cannot be reached through
  the API, which refuses first; it was read in code only.
- **A real Oracle truncate run.** It was not executed, to protect the shared demo data, and `dps_ro` is read-only
  anyway.

## 6. Residue left on the main stack (no values)

All of the following is value-free or synthetic:
- 12 jobs and 5 snapshots of the probe (tenants `default`, `s1m3sec-a`);
- 10 disabled probe keys (`s1m3sec-r…`);
- the `dps_tenant` row `s1m3sec-a`;
- the evidence pack of the scratch `table` run;
- the probe's audit rows.

There is no delete route for jobs or snapshots; retention removes them. All probe connections are deleted, the
scratch MongoDB database is dropped, and no export directory was written.

## 7. Reproduce

All commands run from the main checkout with `source ~/.cache/dps-build/s1-m3-gates-r1/env.sh`:

```bash
P=~/.cache/dps-build/reports/S1-M3-security-r1
.venv/bin/python $P/b6_offline.py $P/b6_offline-results.json          # offline B6 set
.venv/bin/python $P/mongo_offline.py $P $P/mongo_offline-results.json  # offline MongoDB set
.venv/bin/python $P/probe_live.py                                      # writes state-<run>.json
.venv/bin/python $P/probe_followup.py   $P/state-<run>.json
.venv/bin/python $P/probe_tablemode.py  $P/state-<run>.json            # B1
.venv/bin/python $P/probe_tablemode2.py $P/state-<run>.json
.venv/bin/python $P/probe_retry.py $P/state-<run>.json && .venv/bin/python $P/probe_retry2.py $P/state-<run>.json
.venv/bin/python $P/probe_echo.py $P/state-<run>.json
.venv/bin/python $P/scan_values.py $P/state-<run>.json
.venv/bin/python $P/cleanup.py $P/state-<run>.json
```
