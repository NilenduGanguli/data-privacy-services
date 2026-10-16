# M3 G3 implementation report — Tasks 9–14 (MongoDB + engine bridge + conformance)

Worktree `/Users/neelu/document_intelligence/dps-wt-m3`, branch `m3`. Start: clean tree at `d565836`
(G2 fix r2). Author of every commit: `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailers (checked:
0 `Co-Authored-By` lines in `4117faf^..HEAD`). Every deviation is in
`docs/superpowers/build/M3-DEVIATIONS.md` (sections "Task 9" … "Task 14"), committed with its task.

## Commits

| Task | Commit | Message |
|---|---|---|
| 9 | `4117faf` | feat(mongo): BSON type labels, path shape inference from samples, type restoration before writes |
| 10 | `3dbd299` | feat(selection): Mongo filter operator allow-list with $where rejected; Extended JSON literals |
| 11 | `068db5d` | feat(mongo): AsyncMongoClient connector — $sample shapes, majority reads, keyset pages on _id |
| 12 | `b9fdf00` | feat(mongo): batch writer — unordered bulk upserts, transactional in-place and append with the ledger |
| 13 | `a9d032f` | test(connectors): engine bridge on Oracle and Mongo rows; cross-engine token agreement |
| 14 | `6d8bda7` | test(connectors): run the shared conformance suite on Oracle and MongoDB |
| 12+ | `e286dd8` | test(mongo): untouched fields stay byte-identical BSON after an in-place write |

## What was built

- **Task 9 `dps/layers/connectors/mongo_docs.py`**: BSON labels, `infer_shape` (one column per generalised
  leaf path, `_id` first; `_id` is a range key only for codec-round-trippable labels int/long/string/
  objectId/date/uuid), `coerce_bson` (restores Int64/Decimal128/date/ObjectId/uuid at every typed path,
  leaves tokens alone), `make_bson_safe` (copy, never mutates).
- **Task 10 `selection.py`**: allow-list validator rewritten (operators anywhere in the tree, `$where`/
  `$expr`/`$function`/`$accumulator`/`$jsonSchema`/`$text`/`$near`/`$options`/`$comment` denied, also
  inside `$in`/`$eq`/`$all` items and literal embedded documents/arrays); limits (depth 8, 500 nodes, 1000
  items, regex 256); codes `selection_invalid`, `selection_too_complex`, `selection_regex_too_long`
  (mapped into the wizard checklist); Extended-JSON wrappers validated in pure Python at submit time and
  converted by `compile_mongo_filter`; `params`/`where`/`query` with Mongo → `selection_engine_mismatch`.
- **Task 11 `mongo.py` reader**: `MongoConnector.from_secret` (URI or host/params; options allow-list;
  value-free `repr`), `open` (hello/buildInfo), `list_objects`, `describe` (`$sample` ≤ 500), `count`,
  `sample`, `sample_keys` (bounded `$match` before `$sample`, server-sorted), `key_at_offset`,
  `read_partition` (keyset per batch on `_id`, fresh `find` each, `readConcern: majority`, `maxTimeMS`,
  `comment="dps:<action>:<job>"`; key-less collections read through one cursor), deterministic HMAC
  sample also honoured by `count`/`key_at_offset`, `test()` with severities.
- **Task 12 `mongo.py` writer + registry + run handler**: `MongoWriter` (table `ReplaceOne` upserts with
  majority write concern; in_place `$set` of leaf paths and append `InsertOne` inside
  `with_transaction` together with the ledger guard "ledger holds `mark.batch_no - 1`" → else
  `LedgerConflict`; `PkCollision` for duplicate keys; `PrepareFailure` refusals incl.
  `transactions_unsupported`, `path_unsupported`; append key rules and same-collection read bounds
  `append_upper` / new `PrepareReport.append_upper_key`). Registry `mongo` branch. M2 touch
  `jobs_run.append_read_bound` consumes `append_upper_key`.
- **Task 13 `dps/engine/batch.py`**: BatchTransformer now handles document rows (nested and `[*]` paths
  at concrete paths, untouched values by reference, input never mutated via copy-on-write), the
  `unclassified` option (`detect` / `redact` / `keep`) for leaves the shape does not list, and
  `ColumnPlan.ts_precision` (new; fixes `date_shift` on Oracle TIMESTAMP(9) failing every batch with
  `incompatible_method`; `targets.build_column_plans` copies it).
- **Task 14**: `tests/connectors/harness_oracle.py`, `harness_mongo.py`, `test_conformance_oracle.py`,
  `test_conformance_mongo.py`; `conformance.py` gained defaulted env hooks (`names`, `keyless`,
  `composite_keys`, `equals_selection`) — PostgreSQL binding unchanged, no assertion weakened, one added.

## Gates (run through `./lane.sh`, this lane's containers)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` | 283 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success: no issues found in 108 source files |
| `pytest -q` (lane stack up: dps-db, source-pg, oracle, mongo) | **1959 passed, 14 skipped** (204 s), after Task 14 |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "oracle or mongo"` | **132 passed, 1 skipped** (the Mongo composite-key paging case, by capability flag) |
| conformance on all three engines (`test_conformance_{postgres,oracle,mongo}.py`) | 29 passed, 1 skipped (same Mongo composite case) |
| M2 PG integration touched by the `jobs_run` change (`test_run_modes.py`, `test_run_failures.py`, PG conformance) | 13 passed + 11 passed |
| `test_mongo_writer.py` after the last commit | 15 passed |
| Frontend | not touched (not run) |

Skips in the full run: 14 = Mongo composite case + console tests without the app container, OCR mock,
Tesseract, `[ner]` extra not installed (same families as the G2 reviews); none covers code in this group.

## Privacy evidence (counts only, from the tests)

- Source vs target: Mongo table load → `count_documents({"profile.fullName": {"$in": source names}}) == 0`
  in the target; in-place → 0 source names remain; Oracle CHAR/TS9 bridge write → 0 surviving source
  values; wildcard IBANs: 30 originals, none present in output.
- Untouched values: equal + same type for every other column/leaf (Oracle Decimal/float/datetime/bytes/
  FF9 text/padded CHAR; Mongo Int64/Decimal128/datetime/ObjectId), identity preserved in memory, and
  byte-identical BSON in the collection after an in-place write (`e286dd8`).
- Denied filters never reach the server: profiler level 2, 0 `system.profile` entries with the
  connector's comment after `$where`/`$function`/`$expr` attempts.
- Value-free errors: sentinels absent from `str`/`repr`/`args` of connection errors (URI password),
  duplicate-key errors (`PkCollision`, context suppressed), selection errors; log scan via the shared
  conformance `test_no_values_in_logs` on Oracle and Mongo (row names/notes absent from captured logs).
- Mutation checks: removing the ledger guard fails the replay and concurrent-holder tests; moving the
  ledger update out of the transaction fails the commit-together test.

## Deviations (summary; details in M3-DEVIATIONS.md)

1. Mongo: no client `timeoutMS` (measured: it makes an unreachable server wait the full 300 s statement
   timeout); `socketTimeoutMS` + per-read `maxTimeMS` instead.
2. Ledger guard = caller's mark (`batch_no - 1`), `LedgerConflict`; prepare refusals `PrepareFailure`;
   duplicate keys `PkCollision` (plan: `target_error mongo:11000`) — consistent with PG/Oracle as built.
3. Key-less Mongo collections read through one cursor (a mixed-type keyset drops documents).
4. Append key rules stricter (generate needs an all-ObjectId target; same-collection prefix must sort
   above every id and is the bound; same-collection token refused); same-collection generate waits one
   extra second of clock margin.
5. `PIPELINE_ENGINES` keeps Mongo out until Tasks 15/19 adapt the name-keyed pipeline to document paths.
6. `jobs_run.append_read_bound` (M2 touch) consumes `append_upper_key`.
7. `ColumnPlan.ts_precision` (contract §6.4 additive) + `build_column_plans` (M2 touch).
8. Conformance suite: defaulted hooks instead of the plan's registration list (suite as built differs).
9. Validator checks EJSON wrappers at submit time; depth counts nesting levels; new codes in the checklist.
Contract updated in the same commits: §4.2 `append_upper_key`, §5.2 Mongo filter rules, §6.4
`ts_precision` + document rows/unclassified, §12.3 conformance env members.

## Open issues / for later groups

- **Pipeline for Mongo (G4, Tasks 15/19):** discovery, `read_projection`, `build_column_plans` and the run
  handler key columns by *name*; Mongo columns share names (`profile`). Mongo must stay out of
  `PIPELINE_ENGINES` until those use paths; then add it and an e2e test.
- Mongo `table` + `upsert` replaces whole documents (no PG-style "other columns untouched"): decide in
  18b/19 whether the wizard offers upsert for Mongo targets.
- Same-collection `generate` relies on worker clocks within 1 s (NTP); larger skew could produce ids
  below the read bound.
- Mongo key-less read holds one server cursor across batches (10-minute idle timeout); the run handler
  refuses key-less sources anyway (`no_key`).
- From the G2 review (not in this group's scope, unchanged): B6 (the B5 query-alias fix is bypassable) is
  still open per `M3-G2-review-r3.md`.
- `.env.example` still lacks the M3 settings (permission rule; carried from Task 1).

Lane containers were stopped afterwards (`docker compose --profile sources --profile oracle down`, this
project only).
