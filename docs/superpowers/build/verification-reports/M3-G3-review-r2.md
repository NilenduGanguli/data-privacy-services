# M3 G3 review, round 2 (Tasks 9-14): MongoDB rules, filter validation, MongoConnector, MongoWriter, engine bridge, conformance

Reviewer: independent agent, 2026-10-13. Working copy `/Users/neelu/document_intelligence/dps-wt-m3`, branch `m3`,
HEAD `a183f52` (tree clean before and after). I did not rely on `M3-G3-impl.md` or `M3-G3-fix-r1.md`: every
result below comes from reading the code and running it. No repository file was modified. Round-2 probes are in
`~/.cache/dps-build/reports/probe-m3-G3/r2/` and use synthetic data only; each probe drops its own database.

**Verdict: FAIL.** There are 2 blockers, both in the MongoDB writer path with a projected read (`Selection.columns`):

* **R2-B1 (leak):** a projected `in_place` write on an array that mixes scalars with sub-documents lands on the wrong
  element and leaves a source value unmasked. The job reports success.
* **R2-B2 (contract §5.2):** a projected `table` upsert still wipes fields of a matched document when the source
  sub-document lacks the projected leaf. This is the residual of r1 B3.

The round-1 fixes for B1, B2 and B3 are verified as working for the cases they target. All gates are green.

## 1. Gates (all run by me through `./lane.sh`)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` | 284 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success: no issues found in 108 source files |
| `pytest -q` (no services) | 1676 passed, 308 skipped |
| `pytest -q` (lane `dps-db`, `source-pg`, `mongo`, `oracle` up) | **1970 passed, 14 skipped** |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "oracle or mongo"` | **139 passed, 1 skipped** (the skip is Mongo composite paging, from its capability flag) |
| G3 file set with `DPS_TEST_REQUIRE_SERVICES=1` (mongo_docs, selection_mongo/oracle, mongo_connector, mongo_writer, mongo_write_fidelity, engine_bridge_m3, batch_documents_m3, conformance postgres/oracle/mongo, append_bound_m3, registry_m3, pipeline_seams_m3, test_registry) | **199 passed, 1 skipped** |
| `-m "(pg_source or db) and not s3 and not oracle and not mongo"` (covers the M2 touch points `jobs_run.py` and `batch.py`) | 160 passed. There are 3 errors, all in `tests/console/test_console_m2.py` with "app unreachable": those tests need the app container, and no `data-privacy-services:m3` image is built in this lane. G3 touches no console or app code, and the in-process handler tests cover the `jobs_run` change. |

No frontend files changed, so the npm gates do not apply. Logs: `r2/gates_unit.log`, `r2/gates_integration.log`,
`r2/gates_full_services.log`.

## 2. Round-1 blockers: re-verified

| r1 finding | Fix (a183f52) | My check | State |
|---|---|---|---|
| B1: untouched values retyped by the writer | `coerce_bson` removed. The writer only runs `make_bson_safe` on a copy. `BatchTransformer(leaf_restore=…)` restores only the leaves it wrote, to the original leaf's type, and text originals stay text. | r1 `probe_untouched.py`: 0 retyped and 0 altered in `table` and `in_place`; drift `"0042"`/`"007"` stay strings. r2 `probe_r2.py` A: in-sample free-text leaves with no finding 0/30 changed; out-of-sample bool, int32, Int64, double, Decimal128, ObjectId, array and sub-document under a free-text plan are all byte-identical. B: `date_shift` keeps `datetime` 25/25 and changes 25/25. An Int64 `fpe_digits` output is BSON int64 (a Python `int` above int32 encodes as int64). | **fixed** |
| B2: minority `_id` type dropped from range reads | `describe` reads the min and max `_id` and keys only when sample and bounds share one family. A foreign type in the bracket raises `key_type_mixed` on read. | r1 `probe_idtypes.py`: keyed 0/5. r2 D: int ids plus one `Decimal128` id are keyed (same bracket) and the read raises `key_type_mixed` with the value absent from the message; strings plus a `null` id are key-less. `probe_r2d.py`: int32/int64 mix, string, UUID, date and ObjectId keys each read 2500/2500 distinct over 4 partitions. `_prepare` re-runs `describe` (jobs_run l.393), so every run re-checks. | **fixed** |
| B3: projected upsert wipes matched fields | `UpdateOne($set: upsert_fields(doc), upsert=True)` through sub-documents | r1 `probe_upsert.py`: 0/10 lost. **But see R2-B2** (sparse sub-document) and R2-N1 (arrays). | **partly fixed** |

## 3. Blocking findings

### R2-B1: a projected `in_place` write through an array shifts elements and leaves a source value unmasked (privacy leak)

`MongoConnector._projection` passes `Selection.columns` to MongoDB as dotted paths (`{"accounts.iban": 1}`). For an
array, MongoDB's inclusion projection **drops non-document elements**:

* `accounts: [{iban: A}, "legacy-note", {bal: 2}, {iban: B}]` reads back as `[{iban: A}, {}, {iban: B}]`.

`MongoWriter._in_place_ops` then `$set`s positional leaf paths (`docpaths.leaf_updates` gives `accounts.2.iban`)
against the **unprojected** stored array. The masked B is therefore written into element 2, which never had an
IBAN, and the original B at element 3 stays in clear.

Verification (`verify_row`) compares only the projected row, so it passes, and the job reports success.

Probe `r2/probe_r2c.py` (30 regular documents plus 1 mixed-array document, plan `$.accounts[*].iban`
`det_token`, `Selection(columns=["accounts.iban"])`, `in_place`):

* Regular documents: 0/30 surviving IBANs.
* Mixed document: **1 surviving source IBAN**. Element 2 gained an `iban` key, so the source document is also
  corrupted.
* No error was raised.

This is a leak. It contradicts:

* spec and programme plan G3 ("nested arrays transformed");
* the writer's own docstring ("`$set` of the document's leaf paths, so a projected read never drops fields").

`validate_selection` accepts `accounts.iban` for MongoDB, and `in_place` refuses only `query`, not `columns`, so
nothing upstream stops it. The path is latent until T15/T19 put `mongo` in `PIPELINE_ENGINES`, but the defect is in
the Task 11/12 connector and writer as delivered. r1's B1-B3 were judged on the same basis.

Fix direction:

* For `in_place` (the target is the source), read each requested path's **top-level field** (`accounts`, not
  `accounts.iban`), so array positions are exact. Untouched leaves are rewritten with identical BSON, which is
  already proven.
* Or refuse `in_place` with `columns` that cross an array.
* Add an integration test with a mixed scalar/sub-document array (and an array of arrays) that compares the source
  IBANs before and after (0 survivors) and checks the element count and keys.

### R2-B2: a projected `table` upsert still wipes a matched document's sub-document when the source lacks the projected leaf (contract §5.2)

MongoDB returns `profile: {}` for `{"profile.fullName": 1}` on a document whose `profile` exists but has no
`fullName`. This is ordinary in sparse documents. `mongo_docs.upsert_fields` sets **empty sub-documents whole**, so
the upsert sends `$set: {profile: {}}` and the matched target document loses `profile.fullName` **and
`profile.dob`**. `profile.dob` lies outside the projection.

Probe `r2/probe_r2b.py`, source `{profile: {dob: "src-dob"}}` against target
`{profile: {fullName: "TF", dob: "tgt-dob"}}`: the target afterwards holds `profile: {}`. In `r2/probe_r2.py` C, the
`profile: {}` source wipes `profile.dob` the same way. Documents whose projected sub-document has the leaf (B) and
documents with no `profile` (C) keep everything.

This contradicts contract §5.2 as amended in this round: "a matched document keeps every field the row does not
carry … through sub-documents". It is the remaining case of r1 B3, and the new test does not cover it (every source
document there has `profile.fullName`).

Fix direction:

* Do not `$set` an empty sub-document over a matched document. Put it in `$setOnInsert` instead, so an inserted
  document keeps the source's `{}`, or skip it.
* Add a test with a sparse sub-document (and an empty one) in a projected upsert.

## 4. Acceptance (programme plan §2.2 row G3)

| Criterion | Evidence | State |
|---|---|---|
| `$where`/`$function`/`$accumulator`/JS operators rejected | r1 `probe_filters.py` re-run: 31/31 hostile shapes refused (the fullwidth-dollar literal key is harmless). r2 `probe_filters2.py`: 21 more shapes, including `$code`/`$scope` literals, `$dbPointer`, `$symbol`, `$minKey`, `$timestamp`, `$binary`, `$regularExpression`, `$WHERE`, top-level `$expr`, `$where` inside `$elemMatch`→`$and`, `$in`/`$and` with non-lists, `a.b.$c`, a NUL in a key, and a `$where` inside an `$eq` document or a literal sub-document. All are refused except `$not: {$regex}` (legitimate) and an int above 2⁶⁴ (r1 N1). | met |
| Nested arrays transformed | r1 `probe_nested.py` re-run: 0 survivors over `[*][*]`, `[*].cards[*]`, `[*].iban` and `emails[*]`, for full documents. **Not met for projected `in_place` reads over mixed arrays (R2-B1).** | **not met (R2-B1)** |
| Untouched values byte-identical | Writer and transformer: met (section 2). Projected upsert: **not met (R2-B2)**. | **not met (R2-B2)** |
| M2 conformance suite green on Oracle and Mongo | `test_conformance_{postgres,oracle,mongo}.py` green (1 capability skip). No assertion was weakened since r1. | met |

## 5. Deviation log: judgement of the new "Review G3 round 1 fixes" section

* **B1**: restoration moved into `BatchTransformer` via the additive `leaf_restore`; the writer converts nothing but
  `Decimal`/`date`; `jobs_run.leaf_restorer(engine)`; `_transformer(..., *, engine)`.
  * **Accept.** SQL paths are unchanged because the hook is `None` there.
  * Test-input change in `test_mongo_writer.py::_mask` (Int64 risk scores): **accept**. The restoration is now proven
    end to end in `test_mongo_write_fidelity.py`, and no assertion was removed.
  * Integration note: the M5 lane's `_transformer` has 3 call sites and kw-only `tabular`/`constraints`. The merger
    must add `engine=source.engine` at each one. mypy will flag them because `engine` is required. This is not a
    contract break: `_transformer` is private.
* **B2**: id bounds at describe plus the `key_type_mixed` read guard. **Accept.** Exact by BSON bracket ordering,
  re-checked at every prepare. The guard message is static and the value is absent (probe D).
* **B3**: `$set` upserts through sub-documents; a projected array is set whole (documented in contract §4.2/§5.2).
  * **Reject in part.** The empty sub-document rule breaks §5.2 for sparse documents (R2-B2).
  * The array rule is accepted as documented, but see R2-N1.
* **Open item: a key-less collection with `selection.sample` pages `_id` with `$gt` in `_scan_ids`.** **Accept as
  open.** The handler refuses key-less sources (`no_key`), so it only affects a discovery count.

My r1 judgements of the original Task 9-14 entries stand. The rejected Task 12 entry ("`table` always `ReplaceOne`")
is now superseded by the B3 fix.

## 6. Non-blocking notes

* **R2-N1 (forward risk for T15/T19, contract consistency).** `selection.read_projection` folds MongoDB `columns` by
  **top-level name**, while the connector reads the **dotted paths**:
  * `read_projection(shape, Selection(columns=["profile.fullName"]), "mongo")` gives `['_id']`.
  * `["accounts.iban"]` gives `['_id']`.
  * `["profile"]` gives `['_id', 'profile', 'profile']`, with the name duplicated once per leaf path.

  Contract §5.2 (amendment M3 G2 B1) says every connector reads exactly `read_projection` and the handler plans and
  writes exactly it. Once Mongo enters the pipeline, a dotted `columns` would plan nothing, and an `in_place` job
  would "succeed" masking nothing. Not reachable today (`PIPELINE_ENGINES` = {postgres, oracle}). T15/T19 must make
  `read_projection` path-aware for MongoDB (dedupe names, map dotted columns to their leaf columns) or restrict Mongo
  `columns` to top-level fields. Restricting Mongo `columns` to top-level fields would also close R2-B1 and most of
  R2-B2.
* **R2-N2.** A projected upsert overwrites a matched document's array whole. This is documented in contract §4.2/§5.2,
  but it silently drops target-only element fields and any scalar elements the projection removed. Consider refusing
  `table_load: upsert` with `columns` that cross an array, rather than documenting it.
* **R2-N3.** No `datetime_conversion` is set on the client. A BSON date outside Python's range raises `InvalidBSON`
  (not a `PyMongoError`), so `_guard` does not map it. The worker reports it by class name only, so nothing leaks, but
  the job fails as "unexpected" (retry) instead of a coded `source_error`. Consider `DATETIME_AUTO` or mapping
  `bson.errors.InvalidBSON`.
* **R2-N4.** A free-text plan over an out-of-sample `datetime` leaf: the DATE detector tokenises the date part, the
  time stays in clear (`[[PII:DATE:…]]T07:08:09.123000`), and the leaf becomes a string. This is engine (M0) span
  behaviour, not a G3 defect. Noted for the DCE/engine owners.
* **R2-N5.** `fpe_digits` on a short int (fewer digits than FPE needs) falls back to `det_token`, then raises
  `IncompatibleMethod` because a token does not fit an int leaf. That is correct and fail-closed, the same as SQL. The
  preflight surfaces it.
* r1 N1-N8 are unchanged. N5 is now harmless (identical BSON). N8 still applies.

## 7. Probes (round 2, under `probe-m3-G3/r2/`)

| Probe | Result |
|---|---|
| `probe_r2.py` (A free-text drift, B typed planned leaves, C upsert edge cases, D key bounds) | A: 0/30 changed, 8/9 drift types byte-identical (date tokenised, R2-N4). B: see `probe_r2b`. C: `profile: {}` source wipes target `profile.dob` (**R2-B2**); array set whole (R2-N2). D: `key_type_mixed`, value-free; null `_id` makes the collection key-less. |
| `probe_r2b.py` | `date_shift` keeps datetime 25/25; Int64 `fpe_digits` changes 25/25; sparse `profile` source wipes the target sub-document (**R2-B2**) |
| `probe_r2c.py` | **R2-B1**: projected in-place on a mixed array leaves 1 source IBAN in clear and corrupts a neighbour element; 0/30 regular survivors |
| `probe_r2d.py` | 5 `_id` families × 4 partitions: 2500/2500 distinct each |
| `probe_filters2.py` | 19/21 refused; the 2 accepted are legitimate or r1 N1 |
| `r1_probes_rerun.log` | r1 probes after the fix: filters 31/31 refused, nested 0 survivors, untouched 0, id types keyed 0/5, upsert 0/10 lost |

Docker: I started this lane's `dps-db`, `source-pg`, `mongo` and `oracle` (`--profile sources --profile oracle`),
then stopped them with `docker compose down` on this lane's project only.
