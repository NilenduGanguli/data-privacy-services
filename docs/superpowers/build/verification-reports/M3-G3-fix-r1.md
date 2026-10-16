# M3 G3 fix, round 1 (review `M3-G3-review-r1.md` B1, B2, B3)

Worktree `/Users/neelu/document_intelligence/dps-wt-m3`, branch `m3`. Start: HEAD `e286dd8`, tree clean (nothing to resume).
Commit: **`a183f52`** `fix(m3): Mongo writes keep untouched BSON, keyed reads confirm the _id bounds, upserts $set through sub-documents`
(author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailer). Tree clean afterwards. Lane compose project is down.

## Red first

The new integration module `tests/connectors/test_mongo_write_fidelity.py` (7 tests) was run against the pre-fix code with the
`leaf_restore` argument stripped, so it would import. Result: **6 failed, 1 passed**. The one pass was the control test (a uniform collection reads every partition).
- B1 untouched raw BSON: `assert 44 == 0` in both `table` and `in_place`.
- B1 planned leaves keep their type: `10 == 21`.
- B2 keyless: `['primary_key', …] == ['none', …]`.
- B2 in-bracket guard: `DID NOT RAISE`.
- B3: `KeyError: 'segment'`.

Before the fix, the reviewer's probes reproduced 24 retyped leaves, keyed 5/5 with 30000 of 30002 documents read, and 10/10 documents losing fields.

## Fixes

**B1 (untouched values retyped).**
- `MongoWriter._documents` now only runs `make_bson_safe` on a copy. That converts `decimal.Decimal` and `datetime.date`, the two types pymongo cannot encode and never decodes.
- `coerce_bson` is removed.
- Type restoration moved into `BatchTransformer`, which still has each written leaf's original value. It takes a new additive argument `leaf_restore: Callable[[Any, Any], Any] | None = None`, called only for values a plan wrote.
- MongoDB passes `mongo_docs.restore_leaf(original, value)`:
  - It restores `Int64`, int32 (Int64 only when 64 bits are needed), double, `Decimal128`, date and UUID.
  - A value whose type already matches is returned as is.
  - Text that cannot be converted is left as text.
- When the hook is set, a leaf that held text keeps the engine's text. For example, an out-of-sample `"0042"` stays a string.
- `jobs_run.leaf_restorer(engine)` picks the hook: `None` for PostgreSQL and Oracle, so SQL is unchanged. `_transformer` now requires `engine=`, and both call sites pass `source.engine`.
- MongoDB is still outside `PIPELINE_ENGINES` (T15/T19). The wiring is in place so it cannot be forgotten.

**B2 (minority `_id` type dropped).**
- `describe` reads the smallest and largest `_id` with two `_id` index lookups. `infer_shape(…, id_bounds=…)` keys only when the sample and both bounds share one family (`id_label`, where int32 and int64 count as one).
- BSON sorts by bracket first, so this check is exact. A failing collection becomes key-less, and M2 refuses it with `no_key`.
- The run's `_prepare` re-runs `describe`, so the check is repeated before every run.
- A foreign type inside the bracket (a double among ints) is not dropped by range reads, but its key would not round-trip. A keyed `read_partition` now raises a value-free `ConnectorError("key_type_mixed")`.
- A non-UUID binary sorts outside the UUIDs, so the bounds already catch it.

**B3 (projected upsert wiped fields).**
- `table_load: upsert` now writes `UpdateOne({_id}, {$set: mongo_docs.upsert_fields(doc)}, upsert=True)`. Fields are set at dotted paths through sub-documents, so `profile.fullName` keeps `profile.dob`.
- Arrays, scalars and empty sub-documents are set whole.
- A field name containing `.` or starting with `$` raises `PrepareFailure("path_unsupported")`.
- `truncate_insert` and created targets keep `ReplaceOne`.

**Contract:** §4.2 (Mongo key bounds, `key_type_mixed`, the upsert form, "the writer converts nothing else"), §5.2 (Mongo upsert keeps the other fields; a projected array is set whole) and §6.4 (the `leaf_restore` signature and semantics) are amended. All changes are additive. **Deviation log:** new section "Review G3 round 1 fixes" in `docs/superpowers/build/M3-DEVIATIONS.md`.

## Tests added or changed
- `tests/connectors/test_mongo_write_fidelity.py` (new, `integration` + `mongo`):
  - Full unprojected documents in `table` and `in_place` mode: every untouched top-level field is compared as raw BSON element bytes, and **0 changed** (was 44). There are 0 surviving names.
  - A planned `fpe_digits` leaf keeps int32 or Int64 in **21/21** documents (0 equal to the source).
  - 20,000 ObjectIds plus one int and one string `_id`, with a sample of 10: the collection is key-less 3/3, and count 20,002 equals read 20,002.
  - A uniform collection reads 3,000 rows over 4 partitions.
  - A double inside the int bracket raises `key_type_mixed`, and the value is absent from the message.
  - Projected upsert: **10/10** matched documents keep `segment`, `accounts`, `profile.dob` and the target-only field. There are 0 surviving source names, and the inserted documents hold only the projection.
- `tests/connectors/test_mongo_docs.py`:
  - The `coerce_bson` tests are replaced by `restore_leaf` tests (restores, and never converts a matching or untyped value).
  - New `id_bounds` key tests (int, string, document, double and binData bounds).
  - New `upsert_fields` tests.
- `tests/test_batch_documents_m3.py`: the hook is called only for written leaves, and a text leaf stays text.
- `tests/connectors/test_pipeline_seams_m3.py`: both `_transformer` calls pass `engine`, `BatchTransformer` gets `leaf_restore`, and `leaf_restorer` returns the hook only for mongo.
- `tests/connectors/test_mongo_writer.py::_mask` now hands `Int64` risk scores, which is what the transformer emits now. The writer no longer converts plain ints, so this is a test-input change. The restoration it used to prove is now proven end to end by the fidelity test above. No assertion was removed: the Int64 type check remains and now also checks the new value.

## Gates (all through `./lane.sh`)
| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` | 284 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success: no issues found in 108 source files |
| `pytest -q` (whole suite; lane mongo, oracle, source-pg and dps-db up) | **1970 passed, 14 skipped** |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "oracle or mongo"` | **139 passed, 1 skipped** (was 132 plus the 7 new; the skip is Mongo composite paging, from its capability flag) |
| The reviewer's G3 file set + the new fidelity and seams files, `DPS_TEST_REQUIRE_SERVICES=1` | **227 passed, 1 skipped** |
| `-m "(pg_source or db) and not s3 and not oracle and not mongo"` (M2 touch points `jobs_run.py` and `batch.py`) | **160 passed**; 3 errors in `tests/console/test_console_m2.py` ("app unreachable", same as the review: those tests need the app container, which is not started for this group) |
| Reviewer probes after the fix | `probe_untouched`: 0 retyped leaves in table and in_place; drift values stay strings. `probe_upsert`: 0 of 10 lost fields. `probe_idtypes`: keyed 0 of 5 |

No frontend files changed, so the npm gates do not apply. No extras were skipped.
Privacy: the new code logs nothing, error details are static phrases, and all test data is synthetic.

## Open issues (not part of B1-B3)
- A **key-less** collection counted with `selection.sample` still pages `_id`s with `$gt` (`_scan_ids`), which skips other BSON types. M2's run handler refuses key-less sources, so only a discovery count is affected. This is logged in the deviation log.
- A matched document's projected array is overwritten whole by a Mongo upsert, because elements cannot be merged by position. This is documented in contract §4.2 and §5.2.
- The review's notes N1-N8 are unchanged. N5 is now harmless, because in-place `$set` of untouched leaves writes identical BSON.
- `test_console_m2.py` was not run (it needs the app container).
