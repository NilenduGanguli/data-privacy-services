# M3 G3 review, round 1 (Tasks 9-14): MongoDB rules, filter validation, MongoConnector, MongoWriter, engine bridge, conformance

Reviewer: independent agent, 2026-10-13. Working copy `/Users/neelu/document_intelligence/dps-wt-m3`, branch `m3`,
HEAD `e286dd8` (tree clean). I did not rely on `M3-G3-impl.md`; everything below comes from reading the code
and running it. No repository file was modified. Probes are in `~/.cache/dps-build/reports/probe-m3-G3/`.

**Verdict: FAIL.** There are 3 blockers. All three are in the MongoDB writer and reader. Filter validation,
nested-array transforms and the conformance binding are sound.

## 1. Gates (run by me through `./lane.sh`)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` | 283 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success: no issues found in 108 source files |
| `pytest -q` (unit) | **1792 passed, 181 skipped** |
| G3 files with `DPS_TEST_REQUIRE_SERVICES=1` (mongo_docs, selection_mongo/oracle, mongo_connector, mongo_writer, engine_bridge_m3, batch_documents_m3, conformance postgres/oracle/mongo, append_bound_m3, registry_m3, test_registry, test_targets) on the lane's oracle, mongo and source-pg | **210 passed, 1 skipped** (the skip is MongoDB composite paging, from its capability flag) |
| `-m "oracle or mongo"` (whole suite; dps-db up) | **132 passed, 1 skipped** (this matches the implementer's claim; without dps-db, the 9 `test_oracle_pipeline.py` tests error with "dps-db unreachable") |
| `-m "(pg_source or db) and not s3 and not oracle and not mongo"` (the M2 touch points `batch.py`, `jobs_run.py`, `targets.py`) | 160 passed; 3 errors in `tests/console/test_console_m2.py` ("app unreachable": those tests need the app container, which no G3 change touches) |

No frontend files changed, so the npm gates do not apply.

## 2. Blocking findings

### B1: the Mongo writer changes untouched values (BSON type, and sometimes the value)
`MongoWriter._documents` runs `coerce_bson(make_bson_safe(row), source_shape)` over **every typed path of
every document**, not only the leaves the engine converted (`dps/layers/connectors/mongo.py` `_documents`;
`mongo_docs.coerce_bson`/`_coerce`). The rewritten document then reaches the database, either whole
(`table` uses `ReplaceOne`) or through `$set` of every leaf under the written top-level fields (`in_place` uses
`leaf_updates`). Two effects follow:

* **Inside the sample:** when a field holds both int32 and int64 values, the field is labelled `long`, and every untouched
  int32 value is written back as an `Int64`.
* **Outside the sample** (documents the `$sample` of 500 did not see, or documents written after discovery): an
  untouched string in an `int`/`long` field turns into a number (`"0042"` becomes `42`, `"007"` becomes `7`, so the value
  is lost). An ISO string in a `date` field becomes a `datetime`. A 24-hex string in an `objectId` field becomes an
  `ObjectId`. A UUID string becomes a `UUID`.

Probe `probe_untouched.py`: 40 documents, half with int32 and half with Int64 `score`, plus one drift document.
The only plan is `$.name` (`fpe_text`). After the transformer and the writer ran:
`table: untouched leaves with altered BSON type: 24` and `in_place: … 24`. The drift document's `score "0042"`
was stored as `Int64(42)` and `meta.code "007"` as `7`.

This contradicts:
* programme plan §2.2 G3 ("untouched values byte-identical");
* plan Task 13 behaviour 1;
* contract §6.4 as amended in this lane ("values outside every plan leave by reference");
* plan Task 9, where `coerce_bson` restores types *after the engine converted values to text and back*.

The new test `e286dd8` ("byte-identical BSON after an in-place write") does not test the claim. It reads with
`Selection(columns=["profile.fullName"])` and checks only `contact`, `accounts` and `kyc`, which are never
written. `test_untouched_mongo_values_are_identical` checks only the transformer output, before the writer runs.

Fix direction: restore types only where the engine produced a value. One option is for `BatchTransformer` to
restore each written document leaf to its original leaf's BSON type, since it holds the input value. Another is
for the writer to coerce only the planned/written paths. Do not coerce a value whose type already matches its
original. Add a test that writes full (unprojected) documents in `table` and `in_place` mode, with mixed
int32/int64 and an out-of-sample string, and compares raw BSON of every untouched leaf.

### B2: a minority `_id` type outside the describe sample is silently dropped from partitioned reads
`infer_shape` decides `key = ["_id"]` from the `$sample` alone (`mongo_docs.infer_shape`; `describe` samples at
most 500 documents). If the collection also holds `_id`s of another BSON type that the sample missed, the
keyed path runs. MongoDB's type bracketing then hides those documents from every range read: the first
partition is `_id < <ObjectId>`, the later ones `_id >= <ObjectId>`, so an int or string `_id` matches neither.

Probe `probe_idtypes.py`: 30 000 ObjectId documents plus one int `_id` and one string `_id`. Results:
`describe keyed (sample missed minority types): 5 of 5`, then `count=30002 read_over_partitions=30000`.
Two documents were never read, and nothing reported it. The run handler uses `count` only to size partitions
(`jobs_run.py` l.404); it never reconciles the count with the rows read.

Consequences:
* An `in_place` masking job would leave those documents **unmasked** in place, which is a privacy leak.
* A `table` copy would be silently incomplete.

The plan's own rationale for `key_source = "none"` is to avoid exactly this drop, and the module docstring
says so. The writer already checks exactly for `generate` (`find_one({"_id": {"$not": {"$type": "objectId"}}})`).

Fix direction: confirm the `_id` type exactly before treating the collection as keyed. One cheap way is that,
because BSON sorts by type first, `find().sort(_id, 1).limit(1)` and `sort(_id, -1).limit(1)` must both have the
sampled label's type bracket (index-only). Alternatively use the `$not $type` probe. Re-check at
prepare/partition time (the snapshot can be stale). Add a regression test with a large collection and one
foreign-type `_id`.

### B3: a projected `table` load with `upsert` wipes the other fields of matched target documents
`MongoWriter.write_batch` writes every `table` load with `ReplaceOne({_id}, doc, upsert=True)`. A read with
`Selection(columns=…)` yields projected documents, so every matched document in an existing, wider target is
replaced by the projection. Probe `probe_upsert.py`: `matched target docs that lost fields outside the
projection: 10 of 10`.

This contradicts contract §5.2 (amendment 2026-10-13, M3 G2 B1): "an upsert into a wider existing target leaves
the target's other columns untouched on matched rows", stated for **every engine**. The deviation log (Task 12)
admits the difference and defers it to Tasks 18b/19 as an "open question". That deferral contradicts a binding
contract rule this lane wrote itself.

Fix direction, either of:
* `UpdateOne({_id}, {"$set": {<top-level field>: value …}}, upsert=True)` over the projection's top-level
  fields. This avoids the array-path problem the log cites. It overwrites a partially projected sub-document
  whole, so document that or refuse partial sub-document projections for upsert.
* Refuse `table_load: upsert` together with `columns` on MongoDB, value-free, at validation and at prepare,
  and amend the contract.

Add a test either way.

## 3. Acceptance (programme plan §2.2 row G3)

| Criterion | Evidence | State |
|---|---|---|
| `$where`/`$function`/`$accumulator`/JS operators rejected | `test_selection_mongo.py` (62). My `probe_filters.py` tried 31 hostile shapes, including `$where` in `$nor`, `$not`, `$elemMatch` documents, `$all`/`$in` items, literal arrays and nested literals, wrappers holding operators, `$function` under `$eq`/`$regex`/`$size`/`$exists`/`$type`, `$expr` in `$or`, `$text`, `$comment`, `$sampleRate`, `$mod`, geo and bit operators, `a.$where`, positional `a.$` and depth 9. **All refused.** The only one accepted is a fullwidth-dollar key, which is a literal field name and harmless. The profiler test shows a denied filter never reaches the server. | met |
| Nested arrays transformed | `probe_nested.py`: `$.phones[*][*]`, `$.accounts[*].cards[*].pan`, `$.accounts[*].iban`, `$.emails[*]` over 5 documents gave 30/30/10/20 elements with **0 survivors**. Input not mutated; 0 verification hits. | met |
| Untouched values byte-identical | Transformer: yes (values leave by reference). Writer: **no** (B1). | **not met** |
| M2 conformance suite green on Oracle and Mongo | PG, Oracle and Mongo bindings: green (1 skip from a capability flag). The suite diff adds only defaulted engine hooks and one extra assertion (log test count == 1). No assertion was weakened. | met |

## 4. Deviation log, Tasks 9-14: my judgement

**Task 9**
* Range-key label set narrowed to round-trippable types: **accept** (sound).
* `is_key` only when keyed: **accept**.
* `binData` skipped by `coerce_bson`: **accept**.
* `make_bson_safe` copies its input: **accept**.
* Not logged, and part of B1: `coerce_bson` runs over untouched values.

**Task 10**
* Wrappers checked by the validator itself: **accept** (an improvement: submit and the wizard see it).
* Depth counts nesting levels: **accept**.
* `$not` around a wrapper and an empty `$elemMatch` are invalid: **accept**.
* `_CHECKS` mapping: **accept**.
* Contract §5.2 text: **accept**.

**Task 11**
* No client `timeoutMS`; `socketTimeoutMS` plus `maxTimeMS` instead: **accept** (measured and justified).
* Error mapping follows Oracle's (a refused login is not retryable): **accept**.
* Key-less collections read through one cursor: **accept** (correct). It holds one server cursor across batches; see N4.
* `sample_keys` bounded and sorted in the server: **accept**.
* The sample applied in `count`/`key_at_offset`: **accept**.
* `list_objects` filtering, projection collision handling and the `test()` check shape: **accept**.
* Missing safeguard: the key decision relies on the sample (B2).

**Task 12**
* Ledger guard on the caller's mark (`batch_no - 1`), with `LedgerConflict`: **accept** (matches the contract amendment; replay, skip and race tests present).
* `PrepareFailure` and `PkCollision` instead of `ConnectorError`: **accept** (consistent with PG/Oracle).
* Key rules (`generate` needs an all-ObjectId target; same-collection bound waits one second past; prefix must sort above every `_id`; `token` refused on ObjectIds and on the same collection): **accept**. The logged residual (worker clock skew over 1 s) is acceptable as an open issue.
* `table` always `ReplaceOne`: **reject** as long as projected upserts are allowed (B3).
* Write concerns: **accept**.
* `make_bson_safe` before `coerce_bson` on a copy: **accept**, but see B1.
* Registry mongo branch through `load_credentials` and `_flat_secret`: **accept**.
* `PIPELINE_ENGINES` stays {postgres, oracle} until T15/T19: **accept** (prevents jobs that cannot run correctly).
* `append_read_bound` touch point: **accept** (hex-text ObjectId comparison preserves byte order; M2 integer path unchanged).

**Task 13**
* Document paths read and written per concrete path; untouched values leave by reference; input never changed: **accept** (verified).
* Unclassified detect/redact/keep: **accept**.
* `ColumnPlan.ts_precision` (additive contract field): **accept**.
* Per-test markers: **accept**.

**Task 14**
* `ConformanceEnv` bindings instead of a registration list: **accept** (that is how M2 built the suite).
* Defaulted engine hooks; PG binding unchanged; no assertion weakened, one added: **accept**.

## 5. Other checks (no defect found)

* **Error and log hygiene.** `mongo_code` reads only `code` and `writeErrors[0].code`. `BulkWriteError.details`
  and messages are never read; errors are raised `from None`. `repr` names only the database or collection. The
  log events (`mongo_call_failed`) carry only `op` and `driver_code`. The duplicate-key test asserts the sentinel
  is absent from `str`, `repr` and `args`. The conformance no-values-in-logs case runs on all three engines.
* **Atomicity.** `in_place` and `append` commit the batch together with the ledger mark in one `with_transaction`
  (majority). `table` loads are idempotent `_id` upserts. A crash between commit and checkpoint replays nothing
  (conformance `in_place` case on Mongo). There is no half-apply path.
* **Inputs reaching the server.** Every read validates the filter before the call. `selection.columns` is
  validated against `MONGO_PATH` in `validate_selection`. Collection names refuse `$`, an owner and `system.`.
  The host and option names are regex-checked.
* **Contract names.** All contract changes are additive: `PrepareReport.append_upper_key`,
  `ColumnPlan.ts_precision`, `ConformanceEnv` optional members, and the §5.2 Mongo rules. `finish_partition(…, *,
  batch_no=None)` matches the other writers. Other lanes are not broken.
* **Tenant isolation.** The Mongo registry branch resolves credentials through M2's tenant-scoped
  `load_credentials`; there is nothing Mongo-specific to bypass it.

## 6. Non-blocking notes (nits)

* N1: a filter integer above int64 passes validation and then raises a bare `OverflowError` from `bson` during
  the call. That is not a `PyMongoError`, so `_guard` does not map it. The message is value-free, but the job
  fails with an unexpected error instead of `selection_invalid`.
* N2: the node budget does not count scalars. About 500 `$in`/`$nin`/`$all` operators of 1 000 items each
  (around 250 k literals) pass. Consider a total-literal cap.
* N3: `$regex` (at most 256 characters) is bounded only by `maxTimeMS` (the statement timeout) on the server. A
  catastrophic pattern can burn source CPU for that long.
* N4: the key-less read keeps one server cursor between batches. A batch slower than the server's idle-cursor
  timeout (10 min by default) ends in `CursorNotFound`, which maps to `source_error`.
* N5: `in_place` with an empty `write_columns` writes every leaf, so B1 then hits whole documents.
* N6: `_sampler` treats seed `None` and `0` as the same seed (`str(seed or 0)`).
* N7: the ledger stores the source `last_key` in the target database until finalize. This is M2's design, noted
  for the per-column source-vs-target verification at lane verify.
* N8: a flat text column (`$.notes`) holding an array in some documents is skipped by the unclassified scan
  (its name is in `flat_names`). The text plan then serialises the array to one string. The values are masked,
  but the type changes.

## 7. Probes (all under `probe-m3-G3/`, synthetic data, each one drops its own database)

| Probe | Result |
|---|---|
| `gates.log` | lint/type/unit gate output |
| `probe_untouched.py` | **B1**: 24 untouched leaves retyped in both `table` and `in_place`; `"0042"` became 42 |
| `probe_idtypes.py` | **B2**: describe keyed 5 of 5; count 30002, partitioned read 30000 |
| `probe_upsert.py` | **B3**: 10 of 10 matched documents lost fields outside the projection |
| `probe_nested.py` | nested arrays fully transformed, 0 survivors |
| `probe_filters.py` | 31 of 31 hostile filters refused (one harmless literal key accepted) |

Docker: I started this lane's `oracle`, `mongo`, `source-pg` and `dps-db`, and stopped them with
`docker compose down` (this lane's project only) after the run.
