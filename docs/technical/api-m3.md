# Oracle, MongoDB and cross-system job groups (M3)

M3 adds two engines to the M2 job API (`docs/technical/api-m2.md`) and one route. Every M2 route,
convention, error shape, idempotency rule and audit row applies unchanged: an Oracle or MongoDB
connection is created, tested, listed, discovered, reviewed and run exactly like a PostgreSQL one,
and every answer stays value-free (counts, codes, paths, hashes — never a value, literal,
password, URI or driver message).

## Connections

`POST /api/v1/connections` takes the M2 `ConnectionSpec` with `engine: "oracle"` or `"mongo"`.
Credentials never travel in `params` or in environment variables: in production a connection names
a `secret_ref` folder (mounted read-only at `DPS_SECRET_DIR`, `<tenant>/<ref>` or `_shared/<ref>`
per SEC-2) holding one file per field. Inline `credentials` are a dev convenience
(`DPS_INLINE_SECRETS_ALLOWED`, refused by the production posture check), sealed under the KEK.

**Oracle** — secret files `username`, `password` and optionally `dsn` (an easy-connect string such
as `db-host:1521/SERVICE`); without `dsn` the connector builds one from `params.host`, `port`
(default 1521) and `service_name`. Thin mode, no Oracle client libraries.

```json
{
  "name": "crm-ora-ro",
  "engine": "oracle",
  "params": {"host": "oracle", "port": 1521, "service_name": "FREEPDB1"},
  "secret_ref": "dps/src/oracle-crm-ro",
  "roles": ["source"],
  "tags": ["production"]
}
```

**MongoDB** — secret file `uri` (credentials, TLS and replica-set options live in the URI file,
e.g. `mongodb://user:pw@host:27017/crm?replicaSet=rs0&tls=true`), or `username` + `password` with
`params.host`/`port`. The database is `params.database`, else the URI's path (none at all:
`database_required`). One connection reads and writes one database: a target in another database
needs its own connection (the demo uses `mongo-crm-uat` for `crm_uat`). `ObjectRef.owner` is
`null` for MongoDB.

```json
{
  "name": "crm-mdb-uat",
  "engine": "mongo",
  "params": {"database": "crm_uat", "options": {"directConnection": "true"}},
  "secret_ref": "dps/tgt/mongo-crm-uat",
  "roles": ["target"]
}
```

Allowed `params.options` (any other key is a 422 at validation): `authSource`, `replicaSet`
(plain names), `directConnection`, `tls` (`"true"`/`"false"`) for MongoDB; PostgreSQL keeps
`sslmode`, `sslrootcert_ref`, `connect_timeout`. A host, database or option that is not a plain
name is `connection_invalid`.

`POST /connections/{id}/test` runs the engine's least-privilege checks as in M2 (`checks[]` of
`{name, ok, severity}`; `ok` is false only when a `required` check fails): `connect`,
`session_tagged` (advisory: the session carries `module`/`action`/`client_identifier` on Oracle,
the `comment` on MongoDB) and per role the privileges the run needs — on MongoDB `select_source`
(source), `create_target`/`insert_target`/`update_target`/`select_target` (target, by write mode)
and `transactions` (the server is a replica-set member: needed for `in_place` and every `append`).

## Selections per engine

`POST /connections/{id}/count`, discovery and runs take the M2 `Selection`:

| Engine | Form | Rules |
|---|---|---|
| Oracle | `where` (one boolean expression) or `query` (one read-only `SELECT`), `params` bound as `:name` | parsed with sqlglot's Oracle dialect; one statement, no DML/DDL/PL/SQL, no `FOR UPDATE`, no denied functions (`dbms_*`, `utl_*`, `xmltype`, `httpuritype`, …); identifiers `^[A-Za-z][A-Za-z0-9_$#]{0,127}$`; every literal in the text is lifted into a generated bind, so no literal reaches the statement or a log; a `:name` without a value is `selection_param_missing`; a `query` over a key-less table (ROWID partitions) is `selection_query_needs_key` |
| MongoDB | `mongo_filter` (a filter document; `where`/`query` are refused) | operator allow-list `$eq $ne $gt $gte $lt $lte $in $nin $and $or $nor $not $exists $type $size $all $elemMatch $regex`; everything else anywhere in the tree (`$where`, `$expr`, `$function`, `$accumulator`, `$jsonSchema`, `$text`, `$options`, …) is `selection_operator_denied`; field paths `^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$` (`selection_identifier_invalid`); literals are JSON scalars, arrays, plain sub-documents or the Extended-JSON wrappers `{"$oid"}`, `{"$date"}`, `{"$numberLong"}`, `{"$numberDecimal"}` (malformed: `selection_invalid`); at most 8 levels, 500 nodes, 1 000 `$in` items (`selection_too_complex`); a `$regex` of at most 256 characters (`selection_regex_too_long`), case-insensitive matching through the inline `(?i)` flag |

Example MongoDB selection (the evidence pack records it as a template with `?` placeholders, never
the literals):

```json
{"mongo_filter": {"segment": "premium", "kyc.verifiedAt": {"$gte": {"$date": "2024-01-01T00:00:00Z"}}},
 "max_rows": 5000}
```

Discovery samples with `SAMPLE (p) SEED (s)` on Oracle (a hashed, stable row set) and `$sample` on
MongoDB; a document's columns are its leaf paths (`$.contact.email`, `$.accounts[*].iban`).

## Prepare failures (before any row is written)

A run's prepare step refuses with a value-free code (job `failed`, `error_code` set, nothing
written). The M2 codes apply; M3 adds:

| Code | Engine | When |
|---|---|---|
| `transactions_unsupported` | MongoDB | `in_place` or any `append` on a server that is not a replica-set member (the ledger and the batch must commit together) |
| `no_key` | Oracle, MongoDB | Oracle: `in_place` on a key-less table (ROWID partitions are read-only), a `table` upsert without a key (key-less tables load with `truncate_insert`), `append` without a key; MongoDB: `in_place` over a collection whose `_id` values are not of one type; plus M2's rules |
| `column_unsupported` | Oracle | a copied column of a type DPS refuses to move (`LONG`, `LONG RAW`, `BFILE`, `XMLTYPE`, `TIMESTAMP WITH LOCAL TIME ZONE`, `VECTOR`, object types, `ROWID` columns): project it out with `selection.columns` |
| `column_missing` | Oracle | an existing target lacks a column the run writes, or a written column is not in the source shape |
| `target_missing` | Oracle, MongoDB | the target does not exist and `create_if_missing` is false |
| `ledger_conflict` | all | the write ledger (`DPS_WRITE_LEDGER` table / `dps_write_ledger` collection) holds another batch number for the partition: another worker advanced it, or a zombie replays a batch; the batch is not written |
| `path_unsupported` | MongoDB | a field name with `.` or a leading `$` on a written path (cannot be addressed by `$set`) |
| `pk_collision` | all | an appended or upserted key already exists (MongoDB `E11000` is mapped without reading its message) |
| `indexes_not_copied` | MongoDB | not a failure: a `table` target created by DPS gets the `_id` index only; the prepare report lists this check |

Connection-level codes from M3's drivers: `connection_failed` (unreachable, or a refused login —
not retried, so a password is never retried into a lock-out), `statement_timeout`
(`DPS_ORACLE_CALL_TIMEOUT_MS`, MongoDB `maxTimeMS`), `connection_invalid`, `database_required`,
`object_invalid` (a MongoDB object with an `owner`, a `$` or a `system.` name), `object_not_found`, `engine_unavailable` (the driver extra is not installed).
A driver failure carries only its code as detail (`ORA-00942`, `mongo:11000`).

## The consistency check

`POST /api/v1/job-groups/{id}/consistency-check` (scope `jobs`) joins the masked **targets** of a
job group's runs on their shared key entity and reports counts only (contract §6.5, §9). The
result is stored on the group (`last_check`, `last_check_at`; migration 020, `GET
/job-groups/{id}`) and audited as `group.consistency_check` with outcome `passed`/`failed` and
counts.

Request (every field optional):

```json
{
  "pairs": [
    {"job_id": "j_01M4VSSFDQS7XPKVTYDWHBQD7N", "path": "$.CUSTOMER_ID"},
    {"job_id": "j_01M4VSSFDHTPWDSZ007TEHWE2X", "path": "$.customerId"}
  ],
  "sample": 500,
  "min_coverage": 1.0,
  "attributes": true
}
```

- `pairs` (2–8 members, distinct target columns): without it, one member per distinct target is
  derived from the group's succeeded, non-dry-run runs, choosing each target's column classified as
  the shared key entity (`CUSTOMER_ID` first).
- `sample` (10–10 000, default 1 000): values read from the anchor member (the newest run).
- `min_coverage` (0–1, default 1.0): the share of sampled values every other member must hold.
- `attributes`: also compare the other entities the members share on the joined rows.

Join forms: each member's column is detected as `numeric` (integers, leading zeros ignored) or
`prefixed_digits` (`CUST-00123456`: a fixed prefix length and digit width) or `text`; numeric and
prefixed forms join on the integer. This needs a format-preserving key method (`fpe_digits`) on the
join column of every member — `DPS_FPE_ENABLED=true` is the compose default.

Response (`ConsistencyCheckResult`, abridged from the smoke run; no value anywhere — a prefix
appears as its length only):

```json
{
  "group_id": "grp_01M4VSSFCXHGCA1S6C8PQ2ZFZD",
  "sample_requested": 500, "sampled": 500, "anchor": 0,
  "members": [
    {"job_id": "j_…", "engine": "oracle", "connection_id": "c_…", "object": "CRM_MASKED.CUSTOMERS_SMOKE",
     "path": "$.CUSTOMER_ID", "entity_type": "CUSTOMER_ID", "method": "fpe_digits",
     "params_hash": "44136fa355b3", "form": "numeric", "width": null, "prefix_len": null,
     "found": 500, "anchor": true},
    {"job_id": "j_…", "engine": "mongo", "object": "profiles_smoke", "path": "$.customerId",
     "form": "prefixed_digits", "width": 8, "prefix_len": 5, "found": 500, "anchor": false, "…": "…"},
    {"job_id": "j_…", "engine": "postgres", "object": "bank_uat.customers_smoke",
     "path": "$.customer_id", "form": "numeric", "found": 500, "anchor": false, "…": "…"}
  ],
  "pairs": [
    {"left": 0, "right": 1, "checked": 500, "matched": 500, "coverage": 1.0, "consistent": true},
    {"left": 0, "right": 2, "checked": 500, "matched": 500, "coverage": 1.0, "consistent": true},
    {"left": 1, "right": 2, "checked": 500, "matched": 500, "coverage": 1.0, "consistent": true}
  ],
  "all_members_matched": 500,
  "attributes": [
    {"entity_type": "PERSON_NAME", "method": "fpe_text", "members": [0, 1, 2],
     "counts_toward_pass": true, "compared": 500, "agreed": 500, "ambiguous": 0, "consistent": true},
    {"entity_type": "DATE_OF_BIRTH", "method": "date_shift", "members": [0, 1, 2],
     "counts_toward_pass": false, "compared": 500, "agreed": 0, "ambiguous": 0, "consistent": false}
  ],
  "methods_agree": true, "problems": [],
  "passed": true, "consistent_pairs": 3, "total_pairs": 3, "min_coverage": 1.0,
  "checked_at": "2026-10-13T23:33:54.091599Z", "duration_ms": 135.5
}
```

`passed` = no blocking problem, every pair's coverage ≥ `min_coverage`, and every attribute that
`counts_toward_pass` agrees on every compared row. Anchor-dependent methods (`date_shift`, persona
surrogates) agree across systems only when the anchor id is stored in the same form everywhere, so
they are reported as informational (`counts_toward_pass: false`) — the `DATE_OF_BIRTH` row above.

`problems[].code` (with `member` index or `pair`): `method_mismatch` (members protect the join
entity with different methods or parameters — blocking), `kid_mismatch` (a member ran under another
key — blocking), `field_scoped` (a member ran with `consistency.field_scoped=true` — blocking),
`join_form_mismatch` (a member's stored form cannot join the others; that member's pairs fail).

Errors (`{"detail", "code"}`; the detail is fixed text): `group_not_found` 404, `no_shared_entity`
422 (no entity column common to the members), `member_duplicate` 422 (two members name the same
target column), `member_not_in_group` 422, `member_unsupported` 422 (an engine or target the check
cannot read, e.g. a `files` run), `path_unknown` 422, `member_not_ready` 409 (a member is not a
succeeded, non-dry-run run), `secret_unavailable` 503, `connection_failed` 503 / other connector
codes 502 (a target could not be read), `check_timeout` 504 (bounded by
`DPS_CONNECTOR_STATEMENT_TIMEOUT_SECONDS`).

## Throughput

Measured, not promised: see `HANDOFF.md` §12 "Measured throughput (M3)" and the JSON results in
`docs/technical/bench/m3-*.json` (`python tools/bench_connector.py --engine oracle|mongo`).
